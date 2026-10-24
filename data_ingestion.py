"""Concurrent Open-Meteo and Aviation Weather Center ingestion.

Python 3.11+ standard library only. No I/O occurs on import. All external reads
use verified HTTPS, bounded retries, per-host pacing and an expiring memory
cache. Forecasts remain separated by model, preserve missing values and units,
and never silently substitute one model for another. This module sends no alerts.
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import random
import re
import socket
import ssl
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from config import (
    AWC_BASE_URL, FORECAST_MODELS, HOURLY_VARIABLES, ICAO_STATIONS,
    LOCAL_TIMEZONE, MONITORING_ZONES, MPS_TO_KNOTS, OPEN_METEO_URL,
    ForecastModel, GeoPoint, Settings, forecast_locations,
)

JSON = dict[str, Any] | list[Any]


class IngestionError(RuntimeError):
    """An expected service, transport, or response-contract failure."""


class ServiceError(IngestionError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class SchemaError(IngestionError):
    """A provider response violates its expected structure, units or value types."""


class _HttpsRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if urlsplit(new_url).scheme != "https":
            raise ServiceError("Refusing a redirect from HTTPS to an insecure URL")
        return super().redirect_request(request, response, code, message, headers, new_url)


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware datetime is required")
    return value.astimezone(UTC)


def _number(value: Any, name: str, *, nullable: bool = True) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SchemaError(f"{name} must be a finite number" + (" or null" if nullable else ""))
    return float(value)


def _timestamp(value: Any, name: str) -> datetime:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return datetime.fromtimestamp(value, UTC)
        if isinstance(value, str):
            return utc_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OSError, OverflowError) as exc:
        raise SchemaError(f"Invalid {name} timestamp") from exc
    raise SchemaError(f"Missing or invalid {name} timestamp")


@dataclass(frozen=True, slots=True)
class JsonResponse:
    body: JSON
    fetched_at: datetime
    from_cache: bool = False


class JsonHttpClient:
    """Thread-safe bounded cache and rate limiter, shared across provider clients.

    The injected opener/sleep/clock parameters enable deterministic transport
    tests. TLS certificate checks use Python's default trusted context.
    """

    def __init__(self, settings: Settings | None = None, *,
                 opener: Callable[..., Any] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 max_response_bytes: int = 16 * 1024 * 1024, cache_entries: int = 128) -> None:
        self.settings = settings or Settings.from_env()
        if max_response_bytes <= 0 or cache_entries <= 0:
            raise ValueError("Response and cache limits must be positive")
        self.sleep, self.monotonic, self.clock = sleep, monotonic, clock
        self.max_response_bytes, self.cache_entries = max_response_bytes, cache_entries
        self._context = ssl.create_default_context()
        if opener is None:
            verified_opener = build_opener(HTTPSHandler(context=self._context), _HttpsRedirectHandler())
            self.opener = lambda request, *, timeout, context: verified_opener.open(request, timeout=timeout)
        else:
            self.opener = opener
        self._lock = threading.Lock()
        self._next_request: dict[str, float] = {}
        self._cache: OrderedDict[str, tuple[float, bytes, datetime]] = OrderedDict()

    def _pace(self, host: str, cost: float = 1.0) -> None:
        # Recheck after sleeping so a provider cooldown also delays queued workers.
        while True:
            with self._lock:
                now = self.monotonic()
                delay = self._next_request.get(host, now) - now
                if delay <= 0:
                    interval = self.settings.http_min_interval_seconds
                    if host == 'api.open-meteo.com':
                        interval = max(interval,cost*60/self.settings.open_meteo_calls_per_minute)
                    self._next_request[host] = now + interval
                    return
            self.sleep(delay)

    def _retry_delay(self, attempt: int, header: str | None) -> float:
        if header:
            try:
                delay = float(header)
            except ValueError:
                try:
                    delay = (utc_datetime(parsedate_to_datetime(header)) - utc_datetime(self.clock())).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    delay = 0
            if math.isfinite(delay) and delay > 0:
                if delay > 60:
                    raise ServiceError("Provider requests a cooldown longer than 60 seconds; retry later", status=429)
                return delay
        return min(30.0, 2**attempt + random.uniform(0, 0.25))

    @staticmethod
    def _decode(raw: bytes) -> JSON:
        try:
            def invalid_constant(value: str) -> None:
                raise ValueError(f"Nonstandard JSON constant {value}")
            body = json.loads(raw, parse_constant=invalid_constant)
        except (UnicodeDecodeError, ValueError) as exc:
            raise SchemaError("Provider returned invalid JSON") from exc
        if not isinstance(body, (dict, list)):
            raise SchemaError("Provider JSON must be an object or array")
        if isinstance(body, dict) and body.get("error"):
            raise ServiceError("Provider returned an API error: " + str(body.get("reason", "unknown"))[:300])
        return body

    def get_json(self, endpoint: str, params: Mapping[str, Any], *, ttl_seconds: float) -> JsonResponse:
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Endpoint must be a credential-free HTTPS URL without query or fragment")
        if not math.isfinite(ttl_seconds) or ttl_seconds < 0:
            raise ValueError("Cache TTL must be finite and nonnegative")
        url = endpoint + "?" + urlencode(sorted(params.items()))
        with self._lock:
            cached = self._cache.get(url)
            if cached and cached[0] > self.monotonic():
                self._cache.move_to_end(url)
                return JsonResponse(self._decode(cached[1]), cached[2], True)
            self._cache.pop(url, None)
        host = parsed.hostname
        cost = 1.0
        if host == 'api.open-meteo.com':
            locations = len(str(params.get('latitude','')).split(','))
            variables = len(str(params.get('hourly','')).split(','))
            models = len(str(params.get('models','')).split(','))
            days = (float(params.get('forecast_hours',168))+float(params.get('past_hours',0)))/24
            cost = locations*max(1,variables*models/10*max(1,days/14))
        for attempt in range(self.settings.http_max_attempts):
            self._pace(host,cost)
            request = Request(url, headers={"Accept": "application/json", "Accept-Encoding": "identity",
                                           "User-Agent": "duo-discus-weather/0.1 (forecast research)"})
            retry_header = None
            try:
                with self.opener(request, timeout=self.settings.http_timeout_seconds, context=self._context) as response:
                    status = response.status
                    if status == 204:
                        raw = b"[]"
                    else:
                        if status != 200:
                            raise ServiceError(f"{host} returned unexpected HTTP {status}", status=status)
                        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                        if content_type not in {"application/json", "application/geo+json"}:
                            raise SchemaError(f"{host} did not return JSON content")
                        raw = response.read(self.max_response_bytes + 1)
                        if len(raw) > self.max_response_bytes:
                            raise SchemaError("Provider response exceeds the configured size limit")
                body = self._decode(raw)
                fetched_at = utc_datetime(self.clock())
                with self._lock:
                    if ttl_seconds:
                        self._cache[url] = (self.monotonic() + ttl_seconds, raw, fetched_at)
                        while len(self._cache) > self.cache_entries:
                            self._cache.popitem(last=False)
                return JsonResponse(body, fetched_at)
            except HTTPError as exc:
                status = exc.code
                retry_header = exc.headers.get("Retry-After") if exc.headers else None
                # Do not include full URLs, response bodies, or proxy credentials in errors.
                exc.close()
                if status not in {408, 429, 500, 502, 503, 504}:
                    raise ServiceError(f"{host} returned HTTP {status}", status=status) from None
                failure = ServiceError(f"{host} returned HTTP {status} after retries", status=status)
            except (URLError, TimeoutError, socket.timeout, ConnectionError, HTTPException, ssl.SSLError) as exc:
                reason = exc.reason if isinstance(exc, URLError) else exc
                if isinstance(reason, ssl.SSLError) or "403 Forbidden" in str(reason) or "407 Proxy" in str(reason):
                    raise ServiceError(f"{host} connection rejected by proxy or TLS verification") from None
                failure = ServiceError(f"{host} connection failed after retries ({type(reason).__name__})")
            if attempt + 1 == self.settings.http_max_attempts:
                raise failure
            delay = self._retry_delay(attempt, retry_header or ('60' if failure.status == 429 else None))
            with self._lock:
                self._next_request[host] = max(self._next_request.get(host, 0), self.monotonic() + delay)
        raise AssertionError("Positive attempt count is required")


@dataclass(frozen=True, slots=True)
class Forecast:
    location: GeoPoint
    model: str
    fetched_at: datetime
    model_latitude: float
    model_longitude: float
    terrain_elevation_m: float
    times: tuple[datetime, ...]
    hourly: dict[str, tuple[float | None, ...]]
    units: dict[str, str | None]
    missing_counts: dict[str, int]
    warnings: tuple[str, ...]
    from_cache: bool
    requested_horizon_hours: int
    source: str = OPEN_METEO_URL

    def at(self, valid_at: datetime) -> dict[str, float | None]:
        """Exact UTC instant only; never extrapolate or choose a nearby hour."""
        valid_at = utc_datetime(valid_at)
        offset = (valid_at - self.times[0]).total_seconds() if self.times else -1
        index = int(offset // 3600)
        if offset % 3600 or not 0 <= index < len(self.times) or self.times[index] != valid_at:
            raise KeyError(f"Forecast has no sample for {valid_at.isoformat()}")
        return {name: values[index] for name, values in self.hourly.items()}

    @property
    def unavailable_variables(self) -> tuple[str, ...]:
        return tuple(name for name, count in self.missing_counts.items() if count == len(self.times))

    @property
    def available_through(self) -> datetime | None:
        """Last non-null 2m temperature, not a guarantee for other variables."""
        return next((t for t, value in reversed(tuple(zip(self.times, self.hourly["temperature_2m"])))
                     if value is not None), None)


def _expected_units(variable: str) -> set[str]:
    if variable.startswith("vertical_velocity_"):
        return {"m/s", "Pa/s", "hPa/s"}  # Retain units; Module 3 handles omega if necessary.
    if variable.startswith(("wind_speed_", "wind_gusts_")):
        return {"m/s"}
    if variable.startswith("wind_direction_"):
        return {"°", "degree", "degrees"}
    if variable.startswith(("temperature_", "dew_point_")):
        return {"°C"}
    if variable.startswith(("relative_humidity_", "cloud_cover")) or variable == "precipitation_probability":
        return {"%"}
    return {
        "surface_pressure": {"hPa"}, "pressure_msl": {"hPa"}, "precipitation": {"mm"},
        "shortwave_radiation": {"W/m²"}, "direct_radiation": {"W/m²"}, "cape": {"J/kg"},
        "lifted_index": {"K", "°C"}, "boundary_layer_height": {"m"},
        "soil_moisture_0_to_7cm": {"m³/m³"},
        "visibility": {"m"},
    }.get(variable, {"m"} if variable.startswith("geopotential_height_") else set())


def split_model_response(body, model_name):
    """Open-Meteo multi-model columns carry a model suffix; never blend them."""
    suffix='_'+model_name
    return {**body,'hourly':{'time':body.get('hourly',{}).get('time'),
        **{key[:-len(suffix)]:value for key,value in body.get('hourly',{}).items() if key.endswith(suffix)}},
        'hourly_units':{'time':body.get('hourly_units',{}).get('time'),
        **{key[:-len(suffix)]:value for key,value in body.get('hourly_units',{}).items() if key.endswith(suffix)}}}


def parse_forecast(response: JsonResponse, location: GeoPoint, model: ForecastModel, *, variables=HOURLY_VARIABLES) -> Forecast:
    body = response.body
    if not isinstance(body, dict) or not isinstance(body.get("hourly"), dict) or not isinstance(body.get("hourly_units"), dict):
        raise SchemaError("Forecast must contain hourly and hourly_units objects")
    if body.get("utc_offset_seconds") != 0 or body["hourly_units"].get("time") != "unixtime":
        raise SchemaError("Forecast must use UTC Unix timestamps")
    raw_times = body["hourly"].get("time")
    if not isinstance(raw_times, list) or not raw_times:
        raise SchemaError("Forecast has no hourly timestamps")
    times = tuple(_timestamp(value, "forecast") for value in raw_times)
    if any(b - a != timedelta(hours=1) for a, b in zip(times, times[1:])):
        raise SchemaError("Forecast timestamps must be unique, ordered and hourly")
    if len(times) > model.horizon_hours + 25:
        raise SchemaError("Forecast exceeds its requested time window")
    values: dict[str, tuple[float | None, ...]] = {}
    units: dict[str, str | None] = {}
    counts: dict[str, int] = {}
    warnings: list[str] = []
    for variable in variables:
        raw = body["hourly"].get(variable)
        if raw is None:
            raw = [None] * len(times)
        if not isinstance(raw, list) or len(raw) != len(times):
            raise SchemaError(f"{variable} length does not match the time axis")
        series = tuple(_number(value, variable) for value in raw)
        unit = body["hourly_units"].get(variable)
        if unit is not None and not isinstance(unit, str):
            raise SchemaError(f"Invalid unit for {variable}")
        count = sum(value is None for value in series)
        if count < len(times) and unit not in _expected_units(variable):
            raise SchemaError(f"Unexpected or missing unit for {variable}: {unit!r}")
        values[variable], units[variable], counts[variable] = series, unit, count
    if counts["temperature_2m"] == len(times):
        raise SchemaError("Forecast contains no usable surface temperature samples")
    unavailable = [name for name, count in counts.items() if count == len(times)]
    partial = [name for name, count in counts.items() if 0 < count < len(times)]
    if unavailable:
        warnings.append("Unavailable variables: " + ", ".join(unavailable))
    if partial:
        warnings.append("Partially missing variables: " + ", ".join(partial))
    if len(times) < model.horizon_hours + 1 or values["temperature_2m"][-1] is None:
        warnings.append("Requested forecast horizon is incomplete; do not extrapolate to the alert horizon")
    lat = _number(body.get("latitude"), "latitude", nullable=False)
    lon = _number(body.get("longitude"), "longitude", nullable=False)
    elevation = _number(body.get("elevation"), "elevation", nullable=False)
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise SchemaError("Invalid forecast grid coordinates")
    return Forecast(location, model.api_name, response.fetched_at, lat, lon, elevation, times,
                    values, units, counts, tuple(warnings), response.from_cache, model.horizon_hours)


@dataclass(frozen=True, slots=True)
class FetchFailure:
    target: str
    provider: str
    message: str


@dataclass(frozen=True, slots=True)
class ForecastBatch:
    forecasts: tuple[Forecast, ...]
    failures: tuple[FetchFailure, ...]

    def require_success(self) -> None:
        if self.failures:
            raise IngestionError(f"{len(self.failures)} forecast requests failed; inspect batch.failures")


class OpenMeteoClient:
    def __init__(self, settings: Settings | None = None, *, http: JsonHttpClient | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.http = http or JsonHttpClient(self.settings)

    def fetch(self, location: GeoPoint, model: ForecastModel) -> Forecast:
        response = self.http.get_json(OPEN_METEO_URL, {
            "latitude": location.latitude, "longitude": location.longitude,
            "models": model.api_name, "hourly": ",".join(HOURLY_VARIABLES),
            "forecast_hours": model.horizon_hours + 1, "past_hours": 24, "timezone": "GMT", "timeformat": "unixtime",
            "wind_speed_unit": "ms", "temperature_unit": "celsius", "precipitation_unit": "mm",
            "cell_selection": "nearest",
        }, ttl_seconds=self.settings.forecast_cache_seconds)
        return parse_forecast(response, location, model)

    def fetch_many(self, locations: Iterable[GeoPoint] | None = None,
                   models: Iterable[ForecastModel] | None = None) -> ForecastBatch:
        points = tuple(forecast_locations() if locations is None else locations)
        selected = tuple(FORECAST_MODELS.values() if models is None else models)
        if not points or not selected or len(points) > self.settings.max_grid_points:
            raise ValueError("Select nonempty locations/models within the configured point budget")
        if len({point.key for point in points}) != len(points) or len({model.api_name for model in selected}) != len(selected):
            raise ValueError("Location keys and model names must be unique")
        results: dict[tuple[int, int], Forecast] = {}
        failures: dict[tuple[int, int], FetchFailure] = {}
        with ThreadPoolExecutor(max_workers=self.settings.http_workers) as pool:
            jobs = {pool.submit(self.fetch, point, model): (i, j, point, model)
                    for i, point in enumerate(points) for j, model in enumerate(selected)}
            for future in as_completed(jobs):
                i, j, point, model = jobs[future]
                try:
                    results[i, j] = future.result()
                except IngestionError as exc:
                    failures[i, j] = FetchFailure(point.key, model.api_name, str(exc))
        return ForecastBatch(tuple(results[key] for key in sorted(results)),
                             tuple(failures[key] for key in sorted(failures)))

    def fetch_paired(self, locations, *, batch_size=20):
        """Fetch UKMO + ECMWF together, retaining distinct models and missing fields."""
        points=tuple(locations)
        if not points or len(points)>self.settings.max_grid_points or not 1<=batch_size<=50:
            raise ValueError('Invalid paired forecast point budget or batch size')
        models=(FORECAST_MODELS['ukmo'],FORECAST_MODELS['ifs025'])
        def chunk_job(chunk):
            response=self.http.get_json(OPEN_METEO_URL,{
                'latitude':','.join(str(p.latitude) for p in chunk),
                'longitude':','.join(str(p.longitude) for p in chunk),
                'models':','.join(m.api_name for m in models),'hourly':','.join(HOURLY_VARIABLES),
                'forecast_hours':120,'past_hours':24,'timezone':'GMT','timeformat':'unixtime',
                'wind_speed_unit':'ms','temperature_unit':'celsius','precipitation_unit':'mm',
                'cell_selection':'land'},ttl_seconds=self.settings.forecast_cache_seconds)
            bodies=[response.body] if isinstance(response.body,dict) else response.body
            if not isinstance(bodies,list) or len(bodies)!=len(chunk):raise SchemaError('Paired response count does not match request')
            forecasts=[];failures=[]
            for index,(point,body) in enumerate(zip(chunk,bodies)):
                if not isinstance(body,dict) or body.get('location_id',index)!=index:raise SchemaError('Paired response order is invalid')
                for model in models:
                    try:
                        forecasts.append(parse_forecast(JsonResponse(split_model_response(body,model.api_name),response.fetched_at,response.from_cache),point,model))
                    except IngestionError as exc:failures.append(FetchFailure(point.key,model.api_name,str(exc)))
            return forecasts,failures
        forecasts=[];failures=[]
        with ThreadPoolExecutor(max_workers=self.settings.http_workers) as pool:
            jobs={pool.submit(chunk_job,points[i:i+batch_size]):points[i:i+batch_size] for i in range(0,len(points),batch_size)}
            for future in as_completed(jobs):
                try:
                    good,bad=future.result();forecasts.extend(good);failures.extend(bad)
                except IngestionError as exc:
                    failures.extend(FetchFailure(p.key,m.api_name,str(exc)) for p in jobs[future] for m in models)
        return ForecastBatch(tuple(sorted(forecasts,key=lambda f:(f.location.key,f.model))),tuple(failures))

    def fetch_batched(self, locations: Iterable[GeoPoint], models: Iterable[ForecastModel], *,
                      variables: Iterable[str] = HOURLY_VARIABLES, batch_size: int = 20) -> ForecastBatch:
        """Multi-coordinate requests reduce transport overhead, not provider quota.

        A compact variable subset is useful for convergence grids. Unrequested
        fields remain null in the common Forecast schema. Never synthesize them.
        """
        points, selected, fields = tuple(locations), tuple(models), tuple(variables)
        if not points or len(points) > self.settings.max_grid_points or not selected:
            raise ValueError("Locations/models must be nonempty and within the point budget")
        if len({p.key for p in points}) != len(points) or len({m.api_name for m in selected}) != len(selected):
            raise ValueError("Location keys and model names must be unique")
        if not 1 <= batch_size <= 50 or not fields or not set(fields) <= set(HOURLY_VARIABLES):
            raise ValueError("Invalid variable selection or batch size")
        if 'temperature_2m' not in fields:
            raise ValueError("temperature_2m is required for coverage validation")

        def fetch_chunk(chunk, model):
            response = self.http.get_json(OPEN_METEO_URL, {
                'latitude': ','.join(str(p.latitude) for p in chunk),
                'longitude': ','.join(str(p.longitude) for p in chunk),
                'models': model.api_name, 'hourly': ','.join(fields),
                'forecast_hours': model.horizon_hours + 1, 'past_hours': 24, 'timezone': 'GMT', 'timeformat': 'unixtime',
                'wind_speed_unit': 'ms', 'temperature_unit': 'celsius', 'precipitation_unit': 'mm',
                'cell_selection': 'nearest',
            }, ttl_seconds=self.settings.forecast_cache_seconds)
            bodies = [response.body] if isinstance(response.body, dict) else response.body
            if len(bodies) != len(chunk):
                raise SchemaError('Multi-coordinate response count does not match request')
            result = []
            for index, (point, body) in enumerate(zip(chunk, bodies)):
                if not isinstance(body, dict) or body.get('location_id', index) != index:
                    raise SchemaError('Multi-coordinate response order is invalid')
                result.append(parse_forecast(JsonResponse(body, response.fetched_at, response.from_cache), point, model))
            return result

        forecasts, failures = [], []
        with ThreadPoolExecutor(max_workers=self.settings.http_workers) as pool:
            jobs = {pool.submit(fetch_chunk, points[i:i+batch_size], model): (points[i:i+batch_size], model)
                    for model in selected for i in range(0, len(points), batch_size)}
            for future in as_completed(jobs):
                chunk, model = jobs[future]
                try:
                    forecasts.extend(future.result())
                except IngestionError as exc:
                    failures.extend(FetchFailure(p.key, model.api_name, str(exc)) for p in chunk)
        return ForecastBatch(tuple(sorted(forecasts, key=lambda f: (f.location.key, f.model))),
                             tuple(sorted(failures, key=lambda f: (f.target, f.provider))))


@dataclass(frozen=True, slots=True)
class Wind:
    direction_deg: float | None
    speed_knots: float | None
    gust_knots: float | None
    u_mps: float | None  # Eastward velocity, meteorological FROM convention.
    v_mps: float | None  # Northward velocity.
    variable_direction: bool


def parse_wind(record: Mapping[str, Any]) -> Wind:
    direction = record.get("wdir")
    variable = isinstance(direction, str) and direction.upper() == "VRB"
    direction = None if variable else _number(direction, "wind direction")
    speed = _number(record.get("wspd"), "wind speed")
    gust = _number(record.get("wgst"), "wind gust")
    if direction is not None and not 0 <= direction <= 360:
        raise SchemaError("Wind direction is outside 0..360 degrees")
    if any(value is not None and value < 0 for value in (speed, gust)):
        raise SchemaError("Wind speed cannot be negative")
    direction = direction % 360 if direction is not None else None
    if speed == 0:
        u = v = 0.0
    elif speed is None or direction is None:
        u = v = None
    else:
        radians = math.radians(direction)
        u, v = -speed / MPS_TO_KNOTS * math.sin(radians), -speed / MPS_TO_KNOTS * math.cos(radians)
    return Wind(direction, speed, gust, u, v, variable)


@dataclass(frozen=True, slots=True)
class Visibility:
    metres: float | None
    qualifier: str  # EQ, GT, LT or UNKNOWN; retain censored observations.


def parse_visibility(raw: Any) -> Visibility:
    if raw is None:
        return Visibility(None, "UNKNOWN")
    text = str(raw).strip().upper()
    # AWC uses an empty string for visibility omitted from a conditional TAF
    # group. It means "not specified in this group", not zero or unlimited.
    if not text:
        return Visibility(None, "UNKNOWN")
    if text == "CAVOK":
        return Visibility(10000.0, "GT")
    qualifier = "EQ"
    if text.endswith("+") or text.startswith("P"):
        qualifier, text = "GT", text.removesuffix("+").removeprefix("P")
    elif text.startswith("M") or text.startswith("<"):
        qualifier, text = "LT", text[1:]
    text = text.removesuffix("SM").strip()
    try:
        parts = text.split()
        miles = 0.0
        if not 1 <= len(parts) <= 2:
            raise ValueError("Invalid visibility")
        for part in parts:
            if "/" in part:
                numerator, denominator = part.split("/")
                miles += float(numerator) / float(denominator)
            else:
                miles += float(part)
        if not math.isfinite(miles) or miles < 0:
            raise ValueError("Invalid visibility")
    except (ValueError, ZeroDivisionError) as exc:
        raise SchemaError("Invalid AWC visibility in statute miles") from exc
    return Visibility(miles * 1609.344, qualifier)


@dataclass(frozen=True, slots=True)
class CloudLayer:
    cover: str
    base_ft_agl: float | None


def parse_clouds(raw: Any) -> tuple[CloudLayer, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SchemaError("Cloud layers must be an array")
    layers = []
    for layer in raw:
        if not isinstance(layer, dict) or not isinstance(layer.get("cover"), str):
            raise SchemaError("Cloud layer needs a cover code")
        base = _number(layer.get("base"), "cloud base")
        if base is not None and base < 0:
            raise SchemaError("Cloud base cannot be negative")
        layers.append(CloudLayer(layer["cover"].upper(), base))
    return tuple(layers)


def flight_category(visibility: Visibility, clouds: tuple[CloudLayer, ...],
                    vertical_visibility_ft: float | None = None) -> str:
    """US-style METAR display categories, not UK legal VMC minima."""
    if visibility.metres is None:
        return "UNKNOWN"
    miles = visibility.metres / 1609.344
    if visibility.qualifier == "LT":
        return "LIFR" if miles <= 1 else "UNKNOWN"
    if visibility.qualifier == "GT" and miles < 5:
        return "UNKNOWN"
    ceiling_layers = [layer for layer in clouds if layer.cover in {"BKN", "OVC", "VV"}]
    if any(layer.base_ft_agl is None for layer in ceiling_layers) and vertical_visibility_ft is None:
        return "UNKNOWN"
    ceilings = [layer.base_ft_agl for layer in ceiling_layers if layer.base_ft_agl is not None]
    if vertical_visibility_ft is not None:
        ceilings.append(vertical_visibility_ft)
    known_covers = {"BKN", "OVC", "VV", "FEW", "SCT", "CLR", "SKC", "NSC", "NCD"}
    if (not clouds and vertical_visibility_ft is None) or any(layer.cover not in known_covers for layer in clouds):
        return "UNKNOWN"
    ceiling = min(ceilings, default=math.inf)
    if miles < 1 or ceiling < 500:
        return "LIFR"
    if miles < 3 or ceiling < 1000:
        return "IFR"
    if (miles <= 5 and visibility.qualifier != "GT") or ceiling <= 3000:
        return "MVFR"
    return "VFR"


@dataclass(frozen=True, slots=True)
class Metar:
    station: str
    observed_at: datetime
    received_at: datetime | None
    wind: Wind
    visibility: Visibility
    clouds: tuple[CloudLayer, ...]
    qnh_hpa: float | None
    temperature_c: float | None
    dewpoint_c: float | None
    latitude: float | None
    longitude: float | None
    elevation_m: float | None
    flight_category: str
    reported_flight_category: str | None
    stale: bool
    warnings: tuple[str, ...]
    raw: dict[str, Any]


def _station(record: Mapping[str, Any]) -> str:
    station = record.get("icaoId")
    if not isinstance(station, str) or not re.fullmatch(r"[A-Z]{4}", station):
        raise SchemaError("Missing or invalid ICAO station")
    return station


def _qnh(raw: Any) -> float | None:
    qnh = _number(raw, "altimeter pressure")
    if qnh is not None and not 800 <= qnh <= 1100:
        raise SchemaError("AWC altim must be in hPa, within 800..1100; no unit guessing")
    return qnh


def parse_metar(record: Mapping[str, Any], *, now: datetime, max_age_minutes: int = 90) -> Metar:
    station = _station(record)
    observed = _timestamp(record.get("obsTime"), "observation")
    received = _timestamp(record["receiptTime"], "receipt") if record.get("receiptTime") else None
    age = utc_datetime(now) - observed
    warnings = []
    if age > timedelta(minutes=max_age_minutes):
        warnings.append("Observation is stale")
    if age < -timedelta(minutes=5):
        warnings.append("Observation is in the future; check provider/clock")
    clouds = parse_clouds(record.get("clouds"))
    visibility = parse_visibility(record.get("visib"))
    vertical = _number(record.get("vertVis"), "vertical visibility")
    if vertical is not None and vertical < 0:
        raise SchemaError("Vertical visibility cannot be negative")
    reported = record.get("fltCat")
    if reported not in {"VFR", "MVFR", "IFR", "LIFR"}:
        reported = None
    category = flight_category(visibility, clouds, vertical)
    if reported and category != "UNKNOWN" and category != reported:
        warnings.append("Reported flight category differs from decoded visibility/clouds")
    lat, lon = _number(record.get("lat"), "latitude"), _number(record.get("lon"), "longitude")
    if (lat is not None and not -90 <= lat <= 90) or (lon is not None and not -180 <= lon <= 180):
        raise SchemaError("Invalid station coordinates")
    return Metar(station, observed, received, parse_wind(record), visibility, clouds,
                 _qnh(record.get("altim")), _number(record.get("temp"), "temperature"),
                 _number(record.get("dewp"), "dewpoint"), lat, lon,
                 _number(record.get("elev"), "elevation"), category, reported,
                 age > timedelta(minutes=max_age_minutes) or age < -timedelta(minutes=5),
                 tuple(warnings), deepcopy(dict(record)))


@dataclass(frozen=True, slots=True)
class TafPeriod:
    start: datetime
    end: datetime
    change_indicator: str | None
    probability_percent: float | None
    wind: Wind
    visibility: Visibility
    clouds: tuple[CloudLayer, ...]
    qnh_hpa: float | None
    weather: str | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Taf:
    station: str
    issued_at: datetime
    valid_from: datetime
    valid_to: datetime
    periods: tuple[TafPeriod, ...]
    currently_valid: bool
    raw: dict[str, Any]


def parse_taf(record: Mapping[str, Any], *, now: datetime) -> Taf:
    station = _station(record)
    issued = _timestamp(record.get("issueTime"), "TAF issue")
    start = _timestamp(record.get("validTimeFrom"), "TAF validity start")
    end = _timestamp(record.get("validTimeTo"), "TAF validity end")
    if end <= start or issued > end:
        raise SchemaError("Invalid TAF validity interval")
    raw_periods = record.get("fcsts")
    if not isinstance(raw_periods, list) or not raw_periods:
        raise SchemaError("TAF must contain decoded forecast periods")
    periods = []
    for period in raw_periods:
        if not isinstance(period, dict):
            raise SchemaError("TAF period must be an object")
        a, b = _timestamp(period.get("timeFrom"), "TAF period start"), _timestamp(period.get("timeTo"), "TAF period end")
        if not start <= a < b <= end:
            raise SchemaError("TAF period falls outside validity window")
        probability = _number(period.get("probability"), "TAF probability")
        if probability is not None and not 0 <= probability <= 100:
            raise SchemaError("Invalid TAF probability")
        # TEMPO/PROB/BECMG periods overlap; do not flatten them into hourly facts.
        periods.append(TafPeriod(a, b, period.get("fcstChange"), probability, parse_wind(period),
                                 parse_visibility(period.get("visib")), parse_clouds(period.get("clouds")),
                                 _qnh(period.get("altim")), period.get("wxString"), deepcopy(period)))
    current = utc_datetime(now)
    return Taf(station, issued, start, end, tuple(periods), issued <= current and start <= current < end,
               deepcopy(dict(record)))


@dataclass(frozen=True, slots=True)
class AviationBatch:
    metars: dict[str, Metar]
    tafs: dict[str, Taf]
    missing_metars: tuple[str, ...]
    missing_tafs: tuple[str, ...]
    failures: tuple[FetchFailure, ...]
    fetched_at: dict[str, datetime]
    cached_products: tuple[str, ...]


class AviationWeatherClient:
    def __init__(self, settings: Settings | None = None, *, http: JsonHttpClient | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.settings = settings or Settings.from_env()
        self.http = http or JsonHttpClient(self.settings)
        self.clock = clock

    def fetch(self, stations: Iterable[str] = ICAO_STATIONS) -> AviationBatch:
        stations = tuple(dict.fromkeys(station.upper() for station in stations))
        if not stations or len(stations) > 100 or any(not re.fullmatch(r"[A-Z]{4}", s) for s in stations):
            raise ValueError("Select 1..100 four-letter ICAO stations")
        metars: dict[str, Metar] = {}
        tafs: dict[str, Taf] = {}
        failures: list[FetchFailure] = []
        fetched: dict[str, datetime] = {}
        cached = []
        responses = {}
        with ThreadPoolExecutor(max_workers=min(2, self.settings.http_workers)) as pool:
            jobs = {}
            for product in ("metar", "taf"):
                params = {"ids": ",".join(stations), "format": "json"}
                if product == "metar":
                    params["hours"] = max(2, math.ceil(self.settings.metar_max_age_minutes / 60))
                jobs[pool.submit(self.http.get_json, f"{AWC_BASE_URL}/{product}", params,
                                 ttl_seconds=self.settings.aviation_cache_seconds)] = product
            for future in as_completed(jobs):
                product = jobs[future]
                try:
                    responses[product] = future.result()
                except IngestionError as exc:
                    failures.append(FetchFailure("all stations", f"AWC/{product}", str(exc)))
        now = utc_datetime(self.clock())
        for product in ("metar", "taf"):
            response = responses.get(product)
            if response is None:
                continue
            fetched[product] = response.fetched_at
            if response.from_cache:
                cached.append(product)
            if not isinstance(response.body, list):
                failures.append(FetchFailure("all stations", f"AWC/{product}", "Expected a JSON array"))
                continue
            for record in response.body:
                target = "unknown station"
                try:
                    if not isinstance(record, dict):
                        raise SchemaError("Station record must be an object")
                    target = _station(record)
                    if target not in stations:
                        raise SchemaError("Provider returned an unrequested station")
                    if product == "metar":
                        report = parse_metar(record, now=now, max_age_minutes=self.settings.metar_max_age_minutes)
                        previous = metars.get(target)
                        key = (report.observed_at, report.received_at or report.observed_at)
                        if previous is None or key > (previous.observed_at, previous.received_at or previous.observed_at):
                            metars[target] = report
                    else:
                        taf = parse_taf(record, now=now)
                        if target not in tafs or taf.issued_at > tafs[target].issued_at:
                            tafs[target] = taf
                except SchemaError as exc:
                    failures.append(FetchFailure(target, f"AWC/{product}", str(exc)))
        return AviationBatch(metars, tafs, tuple(s for s in stations if s not in metars),
                             tuple(s for s in stations if s not in tafs),
                             tuple(sorted(failures, key=lambda f: (f.provider, f.target, f.message))), fetched, tuple(cached))


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomic output avoids a partially written snapshot after interruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, default=_json_default, allow_nan=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def forecast_from_snapshot(record: Mapping[str, Any]) -> Forecast:
    """Revalidate an exported Forecast rather than trusting serialized arrays."""
    model = next((m for m in FORECAST_MODELS.values() if m.api_name == record.get('model')), None)
    if model is None and record.get('model')=='ukmo_uk_deterministic_2km':
        model=replace(FORECAST_MODELS['ukmo'],api_name='ukmo_uk_deterministic_2km',horizon_hours=48)
    if model is None:
        raise SchemaError('Snapshot has an unknown forecast model')
    location = GeoPoint(**record['location'])
    hourly = {name: list(values) for name, values in record['hourly'].items()}
    hourly['time'] = [_timestamp(value, 'snapshot forecast').timestamp() for value in record['times']]
    body = {'latitude': record['model_latitude'], 'longitude': record['model_longitude'],
            'elevation': record['terrain_elevation_m'], 'utc_offset_seconds': 0,
            'hourly': hourly, 'hourly_units': {**record['units'], 'time': 'unixtime'}}
    return parse_forecast(JsonResponse(body, _timestamp(record['fetched_at'], 'snapshot retrieval'),
                                      bool(record.get('from_cache', False))), location, model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("forecast", "aviation", "all"), default="all")
    parser.add_argument("--region", choices=tuple(MONITORING_ZONES), default="shropshire")
    parser.add_argument("--model", choices=tuple(FORECAST_MODELS) + ("all",), default="all")
    parser.add_argument("--all-locations", action="store_true", help="Query all 9 regions and 12 ridge anchors")
    parser.add_argument("--output", type=Path, help="Write full normalized snapshot as JSON")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    http = JsonHttpClient(settings)
    payload: dict[str, Any] = {"schema_version": 1}
    summary: dict[str, Any] = {}
    failed = False
    if args.mode in {"forecast", "all"}:
        locations = forecast_locations() if args.all_locations else (MONITORING_ZONES[args.region],)
        models = tuple(FORECAST_MODELS.values()) if args.model == "all" else (FORECAST_MODELS[args.model],)
        batch = OpenMeteoClient(settings, http=http).fetch_many(locations, models)
        payload["forecast"] = asdict(batch)
        summary["forecasts"] = [{"location": f.location.key, "model": f.model,
                                 "hours": len(f.times), "available_through": f.available_through,
                                 "first_local_time": f.times[0].astimezone(LOCAL_TIMEZONE),
                                 "unavailable_variables": f.unavailable_variables,
                                 "warnings": f.warnings} for f in batch.forecasts]
        summary["forecast_failures"] = [asdict(f) for f in batch.failures]
        failed |= bool(batch.failures)
    if args.mode in {"aviation", "all"}:
        aviation = AviationWeatherClient(settings, http=http).fetch()
        payload["aviation"] = asdict(aviation)
        summary["aviation"] = {"metars": len(aviation.metars), "tafs": len(aviation.tafs),
                               "missing_metars": aviation.missing_metars, "missing_tafs": aviation.missing_tafs,
                               "stale_metars": [s for s, m in aviation.metars.items() if m.stale],
                               "failures": [asdict(f) for f in aviation.failures]}
        failed |= bool(aviation.failures)
    if args.output:
        _write_json(args.output, payload)
    print(json.dumps(summary, default=_json_default, allow_nan=False, indent=2))
    return 2 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IngestionError, ValueError, OSError) as error:
        import sys
        print(f"Ingestion failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
