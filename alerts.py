"""Six-hour soaring watchdog. Default is a dry run; --send enables delivery.

Creates auditable HTML/JSON briefings even when no threshold is reached. Public
forecast reads are retryable; notification submissions deliberately are not.
SQLite delivery claims protect concurrent/repeated runs and preserve uncertain
outcomes. See docs/operations.md for credentials and durable scheduler state.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
import hashlib
import html
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import numpy as np

from config import (DEFAULT_EMAIL_RECIPIENT, DEFAULT_WHATSAPP_RECIPIENT, FORECAST_MODELS,
                    LOCAL_TIMEZONE, MONITORING_ZONES, ROUTES, SMTP_HOST, SMTP_SSL_PORT,
                    WAVE_RIDGES, Settings, convergence_grid, forecast_locations)
from data_ingestion import (Forecast, IngestionError, OpenMeteoClient, _json_default, _write_json,
                            forecast_from_snapshot, utc_datetime)
from database import AlertKey, DatabaseManager
from day_rating import DayRating,rate_day,best_rating
from soaring_physics import (ConvergenceResult, Profile, TaskDay, ThermalResult, WaveResult,
                             detect_convergence, evaluate_task_day, evaluate_thermals,
                             evaluate_wave, rank_wave_sites, select_forecast)

GRID_VARIABLES = ('temperature_2m', 'dew_point_2m', 'wind_speed_10m', 'wind_direction_10m',
                  'shortwave_radiation') + tuple(f'{name}_{level}hPa' for level in (1000,950,925)
                      for name in ('wind_speed','wind_direction','geopotential_height'))


@dataclass(frozen=True)
class ForecastBundle:
    forecasts: tuple[Forecast, ...]
    grid_forecasts: tuple[Forecast, ...]
    grid_step: float
    errors: tuple[str, ...]
    created_at: datetime


def collect_forecasts(*, grid_step: float = .5, client: OpenMeteoClient | None = None,
                      include_grid: bool = True) -> ForecastBundle:
    client = client or OpenMeteoClient()
    main = client.fetch_batched(forecast_locations(), FORECAST_MODELS.values())
    errors = [f'{f.target} / {f.provider}: {f.message}' for f in main.failures]
    grid_forecasts = ()
    if include_grid:
        grid = convergence_grid(step_degrees=grid_step, max_points=client.settings.max_grid_points)
        batch = client.fetch_batched([p for row in grid for p in row],
                                     (FORECAST_MODELS['icon_eu'], FORECAST_MODELS['ifs025']),
                                     variables=GRID_VARIABLES)
        grid_forecasts = batch.forecasts
        errors.extend(f'{f.target} / {f.provider}: {f.message}' for f in batch.failures)
    return ForecastBundle(main.forecasts, grid_forecasts, grid_step, tuple(errors), datetime.now(UTC))


def save_bundle(bundle: ForecastBundle, path: Path) -> None:
    _write_json(path, {'schema_version': 2, **asdict(bundle)})


def load_bundle(path: Path) -> ForecastBundle:
    raw = json.loads(path.read_text(encoding='utf-8'))
    if raw.get('schema_version') == 1 and 'forecast' in raw:
        records = raw['forecast']['forecasts']
        forecasts = tuple(forecast_from_snapshot(record) for record in records)
        return ForecastBundle(forecasts, (), .5, ('Snapshot has no convergence grid',),
                              max((f.fetched_at for f in forecasts), default=datetime.now(UTC)))
    if raw.get('schema_version') != 2:
        raise ValueError('Unsupported forecast bundle schema')
    return ForecastBundle(tuple(forecast_from_snapshot(r) for r in raw['forecasts']),
                          tuple(forecast_from_snapshot(r) for r in raw['grid_forecasts']), float(raw['grid_step']),
                          tuple(raw['errors']), utc_datetime(datetime.fromisoformat(raw['created_at'])))


def land_mask_for_grid(grid):
    from global_land_mask import globe
    return globe.is_land(np.array([[p.latitude for p in row] for row in grid]),
                         np.array([[p.longitude for p in row] for row in grid]))


def evaluate_convergence(bundle: ForecastBundle, when: datetime, *,
                         preference: tuple[str, ...] = ('icon_eu', 'ecmwf_ifs025')) -> ConvergenceResult | None:
    if not bundle.grid_forecasts:
        return None
    grid = convergence_grid(step_degrees=bundle.grid_step, max_points=10000)
    best = None
    for model in preference:
        result = detect_convergence(grid, bundle.grid_forecasts, when, model, land_mask=land_mask_for_grid(grid))
        if best is None or result.valid_surface_cells > best.valid_surface_cells:
            best = result
        if result.valid_surface_cells >= .8 * result.total_cells:
            return result
    return best


def strong_convergence_regions(result: ConvergenceResult | None) -> tuple[str, ...]:
    """Three adjacent strong cells with dewpoint contrast, avoiding isolated spikes."""
    if result is None or result.valid_surface_cells < .8 * result.total_cells:
        return ()
    regions = set()
    for region in ('Welsh Marches', 'Pennines', 'Southern England'):
        remaining = {(c.row, c.column) for c in result.cells if c.region == region and c.surface_s1 >= 3e-5}
        while remaining:
            component, pending = set(), [remaining.pop()]
            while pending:
                point = pending.pop()
                component.add(point)
                for dr in (-1,0,1):
                    for dc in (-1,0,1):
                        neighbour = point[0]+dr, point[1]+dc
                        if neighbour in remaining:
                            remaining.remove(neighbour)
                            pending.append(neighbour)
            if len(component) >= 3:
                regions.add(region)
    return tuple(sorted(regions))


@dataclass(frozen=True)
class RegionalAnalysis:
    key: str
    name: str
    model: str | None
    thermal: ThermalResult | None
    comparisons: tuple[tuple[str, float | None, float | None], ...]


@dataclass(frozen=True)
class Briefing:
    horizon_days: int
    target_time: datetime
    generated_at: datetime
    regions: tuple[RegionalAnalysis, ...]
    routes: tuple[TaskDay, ...]
    waves: tuple[WaveResult, ...]
    convergence: ConvergenceResult | None
    triggers: tuple[str, ...]
    warnings: tuple[str, ...]
    ratings: tuple[DayRating,...] = ()


def build_briefing(bundle: ForecastBundle, horizon_days: int, *, now: datetime | None = None) -> Briefing:
    if horizon_days not in (2,5):
        raise ValueError('Only 2-day and 5-day horizons are supported')
    now = utc_datetime(now or datetime.now(UTC))
    # Hourly providers cannot represent cron minute 17: compare the enclosing UTC
    # forecast hour at +48/+120 h, explicitly retained in the briefing.
    target = now.replace(minute=0, second=0, microsecond=0) + timedelta(days=horizon_days)
    preference = ('ukmo_uk_deterministic_2km','icon_eu','ecmwf_ifs025') if horizon_days == 2 else ('icon_eu','ecmwf_ifs025')
    regions, warnings, triggers, waves = [], list(bundle.errors), [], []
    for key, point in MONITORING_ZONES.items():
        forecast = select_forecast(bundle.forecasts, key, target, preference)
        if forecast is None:
            regions.append(RegionalAnalysis(key, point.name, None, None, ()))
            warnings.append(f'{point.name}: no forecast at target hour')
            continue
        thermal = evaluate_thermals(Profile.from_forecast(forecast, target))
        comparisons = []
        for model in FORECAST_MODELS.values():
            other = select_forecast(bundle.forecasts, key, target, (model.api_name,))
            if other is not None and other.model != forecast.model:
                estimate = evaluate_thermals(Profile.from_forecast(other, target))
                comparisons.append((other.model, estimate.conservative_climb_knots, estimate.usable_top_ft_amsl))
        regions.append(RegionalAnalysis(key, point.name, forecast.model, thermal, tuple(comparisons)))
        if key == 'shropshire' and thermal.alert_eligible:
            triggers.append('Thermal day: Shropshire')
    regional = {r.key: r for r in regions}
    for route in ROUTES.values():
        if all(regional[p.key].thermal is not None and regional[p.key].thermal.alert_eligible for p in route.waypoints):
            triggers.append(f'Thermal route: {route.name}')
    for key, ridge in WAVE_RIDGES.items():
        forecast = select_forecast(bundle.forecasts, 'ridge:'+key, target,
                                   ('ecmwf_ifs025','ukmo_uk_deterministic_2km','icon_eu'))
        if forecast is not None:
            wave = evaluate_wave(Profile.from_forecast(forecast, target), ridge)
            waves.append(wave)
            if wave.complete and wave.score is not None and wave.score >= 75:
                triggers.append(f'Wave potential: {ridge.point.name} ({wave.score:.0f}/100)')
        else:
            warnings.append(f'{ridge.point.name}: wave forecast unavailable at target hour')
    convergence = evaluate_convergence(bundle, target)
    triggers.extend(f'Strong convergence: {region}' for region in strong_convergence_regions(convergence))
    if convergence is None or convergence.valid_surface_cells < .8 * convergence.total_cells:
        warnings.append('Convergence coverage is incomplete; no convergence alert decision')
    if len(regions) and all(r.thermal is None or r.thermal.conservative_climb_knots is None for r in regions):
        warnings.append('Thermal climb cannot be evaluated at the target hour')
    warnings.extend(('Thermal climbs use radiation/parcel estimates; moisture uncertainty uses the conservative bound for triggers',
                     'Wave rankings and route capacity are planning estimates; use official aviation briefings and operating limits'))
    routes = tuple(evaluate_task_day(route, bundle.forecasts, target.astimezone(LOCAL_TIMEZONE).date(), preference=preference)
                   for route in ROUTES.values())
    ratings=tuple(rate_day(bundle.forecasts,p,target.astimezone(LOCAL_TIMEZONE).date(),preference=preference)
                  for p in MONITORING_ZONES.values())
    for rating in ratings:
        if rating.stars is not None and rating.stars>=3:
            triggers.append(f'{rating.symbols} {rating.stars}-Star day: {rating.scope} · {rating.distance_km:.0f} km local PFD')
    return Briefing(horizon_days, target, now, tuple(regions), routes, rank_wave_sites(waves), convergence,
                    tuple(triggers), tuple(dict.fromkeys(warnings)),ratings)


def _display(value, suffix='', digits=1):
    return 'Unavailable' if value is None else f'{value:.{digits}f}{suffix}'


def render_html(briefing: Briefing) -> str:
    escape = lambda value: html.escape(str(value), quote=True)
    def table(headers, rows):
        return '<table><thead><tr>'+''.join(f'<th>{escape(h)}</th>' for h in headers)+'</tr></thead><tbody>'+''.join(
            '<tr>'+''.join(f'<td>{escape(value)}</td>' for value in row)+'</tr>' for row in rows)+'</tbody></table>'
    rows = []
    for region in briefing.regions:
        t = region.thermal
        rows.append((region.name, region.model or 'Unavailable', _display(t.net_climb_knots if t else None, ' kt'),
                     _display(t.conservative_climb_knots if t else None, ' kt'),
                     _display(t.cloud_base_ft_amsl if t else None, ' ft', 0),
                     _display(t.usable_top_ft_amsl if t else None, ' ft', 0),
                     'Yes' if t and t.overdevelopment else ('No' if t and t.overdevelopment is False else 'Unknown')))
    route_rows = [(ROUTES[r.route].name, f'{r.route_distance_km:.0f} km', *(r.viability[km] for km in (250,300,500,750)), r.route_completion)
                  for r in briefing.routes]
    wave_rows = [(WAVE_RIDGES[w.site].point.name, w.model, _display(w.score, '/100', 0),
                  'Complete' if w.complete else 'Provisional', _display(w.uplift_850_knots, ' kt'), _display(w.uplift_700_knots, ' kt'))
                 for w in briefing.waves]
    convergence = briefing.convergence
    convergence_text = 'Unavailable' if convergence is None else (
        f'{len(convergence.cells)} candidate cells; {convergence.valid_surface_cells}/{convergence.total_cells} grid cells evaluable; '
        f'{convergence.model}; sampling {convergence.spacing_km:.0f} km. '+
        ', '.join(sorted({c.classification for c in convergence.cells})))
    comparisons = [(r.name, model, _display(climb,' kt'), _display(top,' ft',0)) for r in briefing.regions for model,climb,top in r.comparisons]
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Duo Discus soaring watch</title>'
            '<style>body{font:15px Arial,sans-serif;color:#183042;max-width:1100px;margin:24px auto;padding:16px}'
            'table{border-collapse:collapse;width:100%;margin:12px 0 24px}th,td{padding:8px;border-bottom:1px solid #dce4e9;text-align:left}'
            'th{background:#edf4f8}h1,h2{color:#145c75}li{margin:6px 0}</style></head><body>'
            f'<h1>G-CKML · {briefing.horizon_days}-day soaring watch</h1>'
            f'<p>Target: {escape(briefing.target_time.astimezone(LOCAL_TIMEZONE).strftime("%A %d %B %Y, %H:%M %Z"))}. '
            f'Generated {escape(briefing.generated_at.isoformat())}.</p><h2>Triggers</h2><ul>'+
            ''.join(f'<li>{escape(t)}</li>' for t in briefing.triggers or ('No thresholds reached; check data coverage below.',))+'</ul>'+
            '<h2>UK RASP-style day ratings</h2>'+table(('Region','Rating','Local PFD','Net climb','Usable height AGL','Window','Notes'),
              [(r.scope,r.symbols+' · '+r.label,_display(r.distance_km,' km',0),_display(r.climb_knots,' kt'),_display(r.base_agl_ft,' ft',0),_display(r.window_hours,' h'),'; '.join(r.reasons)) for r in briefing.ratings])+
            '<p>Independent distance bands with weather quality caps; not an official RASP product or a verified remote task.</p>'+
            '<h2>Regional thermals</h2>'+table(('Region','Model','Net climb','Conservative climb','Potential cloud base AMSL','Usable top AMSL','Spread-out risk'),rows)+
            '<h2>Task capacity · 10:00–18:00 local</h2>'+table(('Route','Actual length','250 km','300 km','500 km','750 km','Actual route'),route_rows)+
            '<p>Distance variants scale the route legs; supported means idealized continuous soaring capacity, not a cleared flight plan.</p>'+
            '<h2>UK wave rankings</h2>'+table(('Site','Model','Score','Evidence','850 hPa ascent','700 hPa ascent'),wave_rows)+
            '<h2>Convergence</h2><p>'+escape(convergence_text)+'</p>'+
            '<h2>Other model estimates</h2>'+table(('Region','Model','Conservative climb','Usable top AMSL'),comparisons)+
            '<h2>Data quality and assumptions</h2><ul>'+''.join(f'<li>{escape(w)}</li>' for w in briefing.warnings)+
            '</ul></body></html>')


def render_whatsapp(briefing: Briefing) -> str:
    best=best_rating(briefing.ratings)
    text = (f'G-CKML {briefing.horizon_days}-day watch: '
            f'{briefing.target_time.astimezone(LOCAL_TIMEZONE):%a %d %b %H:%M %Z}\n'+
            (f'{best.symbols} {best.label} · {best.scope} · {best.distance_km:.0f} km local PFD\n' if best else 'Day rating unavailable\n')+
            '\n'.join(briefing.triggers[:5])+'\n'+
            '\n'.join(f'Route {r.route}: '+', '.join(f'{km}k {status.replace("_"," ")}' for km,status in r.viability.items()) for r in briefing.routes)+
            '\nPlanning estimates; see email briefing for model gaps and assumptions.')
    return text[:1200]


@dataclass(frozen=True)
class DeliverySettings:
    channels: tuple[str, ...] = ('email','ntfy','whatsapp')
    email_to: str = DEFAULT_EMAIL_RECIPIENT
    smtp_username: str = DEFAULT_EMAIL_RECIPIENT
    gmail_app_password: str = field(default='', repr=False)
    whatsapp_to: str = DEFAULT_WHATSAPP_RECIPIENT
    whatsapp_provider: str = 'callmebot'
    callmebot_api_key: str = field(default='', repr=False)
    twilio_account_sid: str = field(default='', repr=False)
    twilio_auth_token: str = field(default='', repr=False)
    twilio_whatsapp_from: str = ''
    twilio_content_sid: str = ''
    ntfy_topic: str = 'g-ckml-soaring-alerts'

    def __post_init__(self):
        # Google's app-password display groups characters with spaces.
        object.__setattr__(self,'gmail_app_password',''.join(self.gmail_app_password.split()))
        object.__setattr__(self,'callmebot_api_key',self.callmebot_api_key.strip())

    @property
    def skipped_channels(self):
        if 'whatsapp' in self.channels and self.whatsapp_provider=='callmebot' and not self.callmebot_api_key:
            return {'whatsapp':'skipped: CALLMEBOT_API_KEY is unset'}
        return {}

    @property
    def ntfy_url(self):
        return 'https://ntfy.sh/'+self.ntfy_topic

    @classmethod
    def from_env(cls, env: Mapping[str,str] | None = None):
        e = os.environ if env is None else env
        return cls(tuple(dict.fromkeys(c.strip() for c in e.get('DUO_ALERT_CHANNELS','email,ntfy,whatsapp').split(',') if c.strip())),
                   e.get('DUO_EMAIL_TO',DEFAULT_EMAIL_RECIPIENT), e.get('SMTP_USERNAME',DEFAULT_EMAIL_RECIPIENT),
                   e.get('GMAIL_APP_PASSWORD',''), e.get('DUO_WHATSAPP_TO',DEFAULT_WHATSAPP_RECIPIENT),
                   e.get('WHATSAPP_PROVIDER','callmebot').lower(), e.get('CALLMEBOT_API_KEY',''),
                   e.get('TWILIO_ACCOUNT_SID',''), e.get('TWILIO_AUTH_TOKEN',''),
                   e.get('TWILIO_WHATSAPP_FROM',''), e.get('TWILIO_CONTENT_SID',''),
                   e.get('DUO_NTFY_TOPIC','g-ckml-soaring-alerts').strip())

    def validate(self):
        if not self.channels or not set(self.channels) <= {'email','whatsapp','ntfy'}:
            raise ValueError('DUO_ALERT_CHANNELS must select email, ntfy and/or whatsapp')
        if 'ntfy' in self.channels and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',self.ntfy_topic):
            raise ValueError('DUO_NTFY_TOPIC must contain 1–64 letters, digits, underscores or hyphens')
        if 'email' in self.channels:
            if not self.gmail_app_password:
                raise ValueError('Set GMAIL_APP_PASSWORD securely before using --send')
            for address in (self.email_to,self.smtp_username):
                if not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+',address):
                    raise ValueError('Email sender/recipient must be a single valid address')
        if 'whatsapp' in self.channels and 'whatsapp' not in self.skipped_channels:
            if not re.fullmatch(r'\+[1-9]\d{7,14}',self.whatsapp_to):
                raise ValueError('WhatsApp recipient must use E.164 format')
            if self.whatsapp_provider == 'callmebot':
                if not self.callmebot_api_key:
                    raise ValueError('Set CALLMEBOT_API_KEY securely before using --send')
            elif self.whatsapp_provider == 'twilio':
                if not all((self.twilio_account_sid,self.twilio_auth_token,self.twilio_whatsapp_from,self.twilio_content_sid)):
                    raise ValueError('Twilio needs account SID, auth token, WhatsApp sender and an approved TWILIO_CONTENT_SID')
                if not re.fullmatch(r'AC[0-9a-fA-F]{32}',self.twilio_account_sid) or not re.fullmatch(r'HX[0-9a-fA-F]{32}',self.twilio_content_sid):
                    raise ValueError('Invalid Twilio account/content SID format')
                if not re.fullmatch(r'whatsapp:\+[1-9]\d{7,14}',self.twilio_whatsapp_from):
                    raise ValueError('TWILIO_WHATSAPP_FROM must be whatsapp:+number')
            else:
                raise ValueError('WHATSAPP_PROVIDER must be callmebot or twilio')


class DeliveryError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool = True):
        super().__init__(message)
        self.uncertain = uncertain


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise DeliveryError('Notification endpoint redirected; outcome requires review')


def send_email(settings: DeliverySettings, briefing: Briefing, delivery_id: str) -> str:
    message = EmailMessage()
    rating=best_rating(briefing.ratings)
    message['From'], message['To'] = settings.smtp_username, settings.email_to
    message['Subject'] = f'G-CKML: {briefing.horizon_days}-day soaring watch · {briefing.target_time:%d %b}'
    if rating:
        message.replace_header('Subject',f'G-CKML: {rating.stars}-Star day · {briefing.horizon_days}-day watch · {briefing.target_time:%d %b}')
        if rating.stars>=3:message['X-Priority']='1';message['Importance']='high'
    message['Message-ID'] = f'<{delivery_id}@duo-discus-weather.local>'
    message.set_content(render_whatsapp(briefing))
    message.add_alternative(render_html(briefing), subtype='html')
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_SSL_PORT, timeout=30, context=ssl.create_default_context()) as smtp:
            smtp.login(settings.smtp_username,settings.gmail_app_password)
            refused = smtp.send_message(message)
            if refused:
                raise DeliveryError('SMTP recipient rejected',uncertain=False)
    except (smtplib.SMTPAuthenticationError,smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError) as exc:
        raise DeliveryError(f'SMTP rejected notification ({type(exc).__name__})',uncertain=False) from None
    except (OSError,smtplib.SMTPException) as exc:
        raise DeliveryError(f'SMTP outcome uncertain ({type(exc).__name__})') from None
    return str(message['Message-ID'])


def send_whatsapp(settings: DeliverySettings, briefing: Briefing, delivery_id: str) -> str:
    text = render_whatsapp(briefing)
    if settings.whatsapp_provider == 'callmebot':
        query = urlencode({'phone':settings.whatsapp_to,'text':text,'apikey':settings.callmebot_api_key})
        request = Request('https://api.callmebot.com/whatsapp.php?'+query,headers={'User-Agent':'duo-discus-weather/1.0'})
    else:
        # WhatsApp template parameters cannot contain newline/tab runs. Leave
        # room for the approved template's fixed text within its body limit.
        template_text = ' '.join(text.split())[:900]
        body = urlencode({'To':'whatsapp:'+settings.whatsapp_to,'From':settings.twilio_whatsapp_from,
                          'ContentSid':settings.twilio_content_sid,'ContentVariables':json.dumps({'1':template_text})}).encode()
        authorization = base64.b64encode(f'{settings.twilio_account_sid}:{settings.twilio_auth_token}'.encode()).decode()
        request = Request(f'https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json',
                          data=body,headers={'Authorization':'Basic '+authorization,'Content-Type':'application/x-www-form-urlencoded'},method='POST')
    try:
        opener = build_opener(HTTPSHandler(context=ssl.create_default_context()),_NoRedirect())
        with opener.open(request,timeout=30) as response:
            status, raw = response.status, response.read(65537)
        if len(raw) > 65536:
            raise DeliveryError('Notification response too large; outcome uncertain')
        if settings.whatsapp_provider == 'twilio':
            result = json.loads(raw)
            if status != 201 or not isinstance(result,dict) or not re.fullmatch(r'(SM|MM)[0-9a-fA-F]{32}',str(result.get('sid',''))):
                raise DeliveryError('Twilio acceptance not confirmed')
            return result['sid']
        body = raw.decode('utf-8',errors='replace').lower()
        if status == 200 and ('message queued' in body or 'message successfully sent' in body):
            return 'callmebot:'+delivery_id
        raise DeliveryError('CallMeBot acceptance not confirmed; review provider outcome')
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise DeliveryError(f'WhatsApp provider returned HTTP {code}',uncertain=code >= 500 or code in (408,429)) from None
    except (URLError,OSError,ValueError) as exc:
        raise DeliveryError(f'WhatsApp outcome uncertain ({type(exc).__name__})') from None


def send_ntfy(settings: DeliverySettings, briefing: Briefing, delivery_id: str) -> str:
    """Keyless public-topic push; submit once and require explicit acceptance."""
    message=render_whatsapp(briefing).encode('utf-8')[:4000].decode('utf-8',errors='ignore').encode('utf-8')
    request=Request(settings.ntfy_url,data=message,method='POST',
        headers={'Content-Type':'text/plain; charset=utf-8','User-Agent':'duo-discus-weather/1.0',
                 'Title':f'G-CKML: {briefing.horizon_days}-day soaring watch','Tags':'airplane',
                 'Priority':'high' if (best_rating(briefing.ratings) and best_rating(briefing.ratings).stars>=3) else 'default'})
    try:
        opener=build_opener(HTTPSHandler(context=ssl.create_default_context()),_NoRedirect())
        with opener.open(request,timeout=30) as response:
            status,raw=response.status,response.read(65537)
        if len(raw)>65536:raise DeliveryError('ntfy response too large; outcome uncertain')
        result=json.loads(raw)
        if (status!=200 or not isinstance(result,dict) or result.get('event')!='message'
            or result.get('topic')!=settings.ntfy_topic
            or not isinstance(result.get('id'),str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',result['id'])):
            raise DeliveryError('ntfy acceptance not confirmed')
        return 'ntfy:'+result['id']
    except HTTPError as exc:
        code=exc.code;exc.close()
        raise DeliveryError(f'ntfy returned HTTP {code}',uncertain=code>=500 or code in (408,429)) from None
    except (URLError,OSError,ValueError) as exc:
        raise DeliveryError(f'ntfy outcome uncertain ({type(exc).__name__})') from None


def dispatch(briefing: Briefing, database: DatabaseManager, settings: DeliverySettings, *,
             senders: Mapping[str,Callable] | None = None) -> dict[str,str]:
    settings.validate()
    if not briefing.triggers:
        return {}
    senders = senders or {'email':send_email,'whatsapp':send_whatsapp,'ntfy':send_ntfy}
    outcomes = settings.skipped_channels.copy()
    for channel in settings.channels:
        if channel in settings.skipped_channels:continue
        recipient = {'email':settings.email_to.lower(),'whatsapp':settings.whatsapp_to,'ntfy':settings.ntfy_url}[channel]
        key = AlertKey(briefing.target_time.astimezone(LOCAL_TIMEZONE).date(),briefing.horizon_days,
                       'briefing','national',channel,recipient)
        claim = database.claim_alert(key,payload={'triggers':list(briefing.triggers),'target_time':briefing.target_time.isoformat()})
        if claim is None:
            outcomes[channel] = 'already sent, reserved or unresolved'
            continue
        delivery_id = hashlib.sha256(json.dumps(key.values).encode()).hexdigest()[:32]
        database.start_delivery(claim)
        try:
            provider_id = senders[channel](settings,briefing,delivery_id)
        except DeliveryError as exc:
            database.mark_failed(claim,error=str(exc),uncertain=exc.uncertain)
            outcomes[channel] = 'uncertain' if exc.uncertain else 'failed'
        except Exception as exc:
            # Preserve the unresolved send, without exposing provider URLs/secrets.
            database.mark_failed(claim,error=f'Unexpected sender failure: {type(exc).__name__}',uncertain=True)
            outcomes[channel] = 'uncertain'
        else:
            database.mark_sent(claim,provider_message_id=provider_id)
            outcomes[channel] = 'accepted'
    return outcomes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--send',action='store_true',help='Submit notifications using configured credentials')
    mode.add_argument('--dry-run',action='store_true',help='Evaluate and render only (the default)')
    parser.add_argument('--snapshot',type=Path,help='Use an existing forecast bundle')
    parser.add_argument('--output-dir',type=Path,default=Path('var/briefings'))
    parser.add_argument('--grid-step',type=float,default=.5)
    parser.add_argument('--at',help='Timezone-aware evaluation time, for dry-run replay only')
    args = parser.parse_args(argv)
    settings = DeliverySettings.from_env()
    if args.send:
        settings.validate()
        if args.at:
            parser.error('--at is only allowed with dry runs')
    now = utc_datetime(datetime.fromisoformat(args.at)) if args.at else datetime.now(UTC)
    bundle = load_bundle(args.snapshot) if args.snapshot else collect_forecasts(grid_step=args.grid_step)
    if args.send and any(now-f.fetched_at > timedelta(hours=2) or f.fetched_at > now+timedelta(minutes=5)
                         for f in (*bundle.forecasts,*bundle.grid_forecasts)):
        raise ValueError('Snapshot is stale or future-dated; refresh before sending')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    if not args.snapshot:
        save_bundle(bundle,args.output_dir/'forecast-bundle.json')
    database = DatabaseManager() if args.send else None
    summary, failed = [], bool(bundle.errors)
    for horizon in (5,2):
        briefing = build_briefing(bundle,horizon,now=now)
        (args.output_dir/f'briefing-{horizon}day.html').write_text(render_html(briefing),encoding='utf-8')
        _write_json(args.output_dir/f'briefing-{horizon}day.json',asdict(briefing))
        outcomes = dispatch(briefing,database,settings) if args.send else {}
        # Push supplements Gmail. Report its failure without failing a working
        # email run; a push-only run still fails when delivery is unconfirmed.
        failed |= any(value in {'failed','uncertain'} for channel,value in outcomes.items()
                      if channel!='ntfy' or 'email' not in settings.channels)
        # No data is an evaluation failure, never an all-clear briefing.
        failed |= all(r.thermal is None for r in briefing.regions)
        summary.append({'horizon_days':horizon,'target_time':briefing.target_time,
                        'triggers':briefing.triggers,'delivery':outcomes,'warnings':briefing.warnings})
    print(json.dumps({'mode':'send' if args.send else 'dry-run','briefings':summary},default=_json_default,indent=2))
    return 2 if failed else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (IngestionError,ValueError,OSError) as error:
        import sys
        print(f'Watchdog failed: {error}',file=sys.stderr)
        raise SystemExit(2) from None
