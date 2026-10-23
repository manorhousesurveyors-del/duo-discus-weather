"""Unit-aware soaring diagnostics and idealized Duo Discus task planning.

Pure calculations: no network calls, notification dispatch, or database writes.
Missing inputs produce explicit unavailable results. Sparse pressure profiles,
radiation-derived heat flux, wave scores and front classifications are estimates,
not resolved lift forecasts. Methods and thresholds are in docs/physics.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import math
from typing import Iterable, Mapping

import numpy as np

from config import (CIRCLING_SINK_KNOTS, DUO_DISCUS_POLARS, LOCAL_TIMEZONE,
                    METRES_TO_FEET, MPS_TO_KNOTS, PRESSURE_LEVELS_HPA, ROUTES,
                    TASK_DISTANCES_KM, GeoPoint, Polar, Route, WaveRidge)
from data_ingestion import Forecast, utc_datetime

G = 9.80665
RD = 287.05
CP = 1004.0
EARTH_RADIUS_M = 6_371_008.8


def angular_difference(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def wind_components(speed_mps: float, direction_deg: float) -> tuple[float, float]:
    if not math.isfinite(speed_mps) or speed_mps < 0 or not math.isfinite(direction_deg):
        raise ValueError('Wind must have finite direction and nonnegative speed')
    angle = math.radians(direction_deg)
    return -speed_mps * math.sin(angle), -speed_mps * math.cos(angle)


def wind_direction(u: float, v: float) -> float | None:
    return math.degrees(math.atan2(-u, -v)) % 360 if math.hypot(u, v) > 1e-6 else None


def distance_bearing(a: GeoPoint, b: GeoPoint) -> tuple[float, float]:
    p1, p2 = math.radians(a.latitude), math.radians(b.latitude)
    dp, dl = p2 - p1, math.radians(b.longitude - a.longitude)
    hav = math.sin(dp / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2)**2
    distance = 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1, max(0, hav)))) / 1000
    bearing = math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                    math.cos(p1)*math.sin(p2)-math.sin(p1)*math.cos(p2)*math.cos(dl))) % 360
    return distance, bearing


def dewpoint_c(temperature_c: float, rh_percent: float) -> float | None:
    if not 0 < rh_percent <= 100 or temperature_c <= -243.04:
        return None
    gamma = math.log(rh_percent / 100) + 17.625 * temperature_c / (243.04 + temperature_c)
    return 243.04 * gamma / (17.625 - gamma)


def vertical_velocity_mps(value: float, unit: str, pressure_hpa: float, temperature_c: float) -> float:
    """Positive upward w=-omega/(rho*g), with omega positive downward in Pa/s."""
    if not all(math.isfinite(x) for x in (value, pressure_hpa, temperature_c)) or pressure_hpa <= 0 or temperature_c <= -273.15:
        raise ValueError('Invalid vertical-velocity inputs')
    if unit == 'm/s':
        return value
    if unit not in {'Pa/s', 'hPa/s'}:
        raise ValueError(f'Unsupported vertical velocity unit: {unit}')
    density = pressure_hpa * 100 / (RD * (temperature_c + 273.15))
    return -value * (100 if unit == 'hPa/s' else 1) / (density * G)


@dataclass(frozen=True)
class Level:
    pressure_hpa: float
    height_m: float
    temperature_c: float
    rh_percent: float | None
    u_mps: float | None
    v_mps: float | None
    w_mps: float | None
    interpolated: bool = False

    @property
    def theta_k(self) -> float:
        return (self.temperature_c + 273.15) * (1000 / self.pressure_hpa)**(RD / CP)


@dataclass(frozen=True)
class Profile:
    forecast: Forecast
    valid_at: datetime
    values: Mapping[str, float | None]
    levels: tuple[Level, ...]

    @classmethod
    def from_forecast(cls, forecast: Forecast, valid_at: datetime) -> Profile:
        values = forecast.at(valid_at)
        levels = []
        for pressure in PRESSURE_LEVELS_HPA:
            suffix = f'_{pressure}hPa'
            temperature, height = values.get('temperature'+suffix), values.get('geopotential_height'+suffix)
            if temperature is None or height is None or height <= forecast.terrain_elevation_m + 2:
                continue  # Pressure levels below model terrain are not atmospheric samples.
            speed, direction = values.get('wind_speed'+suffix), values.get('wind_direction'+suffix)
            u, v = wind_components(speed, direction) if speed is not None and direction is not None else (None, None)
            raw_w = values.get('vertical_velocity'+suffix)
            w = vertical_velocity_mps(raw_w, forecast.units['vertical_velocity'+suffix], pressure, temperature) if raw_w is not None else None
            levels.append(Level(pressure, height, temperature, values.get('relative_humidity'+suffix), u, v, w))
        levels.sort(key=lambda level: level.height_m)
        if any(b.height_m <= a.height_m or b.pressure_hpa >= a.pressure_hpa for a, b in zip(levels, levels[1:])):
            raise ValueError('Pressure and height are not monotonically ordered')
        return cls(forecast, utc_datetime(valid_at), values, tuple(levels))

    def level(self, pressure: float) -> Level | None:
        """Log-pressure interpolation of scalars and wind components; no extrapolation."""
        for level in self.levels:
            if level.pressure_hpa == pressure:
                return level
        for lower, upper in zip(self.levels, self.levels[1:]):
            if upper.pressure_hpa < pressure < lower.pressure_hpa:
                weight = math.log(pressure / lower.pressure_hpa) / math.log(upper.pressure_hpa / lower.pressure_hpa)
                def interpolate(name):
                    a, b = getattr(lower, name), getattr(upper, name)
                    return None if a is None or b is None else a + weight * (b - a)
                return Level(pressure, *(interpolate(name) for name in (
                    'height_m', 'temperature_c', 'rh_percent', 'u_mps', 'v_mps', 'w_mps')), interpolated=True)
        return None


def boundary_layer_height(profile: Profile) -> tuple[float | None, str]:
    direct = profile.values.get('boundary_layer_height')
    if direct is not None and direct >= 0:
        return direct, 'model boundary-layer height'
    temperature, pressure = profile.values.get('temperature_2m'), profile.values.get('surface_pressure')
    if temperature is None or pressure is None or pressure <= 0:
        return None, 'missing surface temperature/pressure for parcel estimate'
    # A dry surface parcel without an arbitrary thermal excess. First crossing
    # of environmental potential temperature bounds dry mixing depth.
    parcel_theta = (temperature + 273.15) * (1000 / pressure)**(RD / CP)
    previous_height, previous_delta = profile.forecast.terrain_elevation_m + 2, -0.01
    for level in profile.levels:
        delta = level.theta_k - parcel_theta
        if delta > 0:
            fraction = -previous_delta / (delta - previous_delta)
            height = previous_height + fraction * (level.height_m - previous_height)
            return max(0, height - profile.forecast.terrain_elevation_m), 'estimated dry-parcel mixing depth'
        previous_height, previous_delta = level.height_m, delta
    return None, 'parcel top not bracketed by available profile; no extrapolation'


@dataclass(frozen=True)
class ThermalResult:
    net_climb_knots: float | None
    conservative_climb_knots: float | None
    cloud_base_ft_amsl: float | None
    usable_top_ft_amsl: float | None
    boundary_layer_m_agl: float | None
    sensible_heat_flux_wm2: float | None
    convective_velocity_mps: float | None
    climb_35: bool | None
    climb_45: bool | None
    base_4500: bool | None
    base_5000: bool | None
    overdevelopment: bool | None
    cloud_streets: bool | None
    cumulus_supported: bool | None
    estimated: bool
    notes: tuple[str, ...]

    @property
    def alert_eligible(self) -> bool:
        return (self.conservative_climb_knots is not None and self.conservative_climb_knots >= 3.5
                and self.usable_top_ft_amsl is not None and self.usable_top_ft_amsl >= 4500)


def evaluate_thermals(profile: Profile) -> ThermalResult:
    p, values = profile.forecast, profile.values
    temperature, dewpoint = values.get('temperature_2m'), values.get('dew_point_2m')
    depth, depth_source = boundary_layer_height(profile)
    notes = [depth_source]
    base = None if temperature is None or dewpoint is None else max(0, temperature-dewpoint) * 400 + p.terrain_elevation_m * METRES_TO_FEET
    top = None if depth is None else (p.terrain_elevation_m + depth) * METRES_TO_FEET
    usable = min(base, top) if base is not None and top is not None else None
    cumulus = None if base is None or top is None else base <= top + 200
    if cumulus is False:
        notes.append('Dry mixing top is below condensation level: blue thermals possible; cloud base is a potential LCL')
    radiation, soil, pressure = values.get('direct_radiation'), values.get('soil_moisture_0_to_7cm'), values.get('surface_pressure')
    heat = velocity = net = conservative = None
    if all(value is not None for value in (radiation, temperature, pressure, depth)) and pressure > 0:
        available = max(0, radiation) * 0.8 * 0.7  # Albedo 0.2, available-energy fraction 0.7.
        if soil is None:
            bowen, low_bowen = 1.0, 0.25
            notes.append('Soil moisture unavailable: Bowen ratio 1 nominal, 0.25 conservative')
        else:
            wetness = min(1, max(0, (soil - 0.05) / 0.35))
            bowen = low_bowen = 4 * (1-wetness) + 0.25 * wetness
        heat = available * bowen / (1 + bowen)
        low_heat = available * low_bowen / (1 + low_bowen)
        density = pressure * 100 / (RD * (temperature + 273.15))
        def climb(flux):
            w = (G / (temperature+273.15) * flux / (density*CP) * max(0, depth))**(1/3)
            return w, w * MPS_TO_KNOTS - CIRCLING_SINK_KNOTS
        velocity, net = climb(heat)
        _, conservative = climb(low_heat)
        notes.append('Radiation-derived heat flux and w* are proxies for thermal core climb')
    else:
        notes.append('Climb unavailable: need temperature, pressure, direct radiation and bracketed mixing depth')
    nearest = min(profile.levels, key=lambda level: abs(level.height_m-(p.terrain_elevation_m+depth))) if depth is not None and profile.levels else None
    low_cloud = values.get('cloud_cover_low')
    overdevelopment = None if nearest is None or nearest.rh_percent is None or low_cloud is None else nearest.rh_percent > 85 and low_cloud > 60
    surface_direction = values.get('wind_direction_10m')
    candidates = [level for level in (profile.level(925), profile.level(850)) if level is not None
                  and depth is not None and level.height_m <= p.terrain_elevation_m + depth
                  and level.u_mps is not None and level.v_mps is not None]
    streets = None
    if candidates and nearest and nearest.u_mps is not None and surface_direction is not None:
        inverse_direction = wind_direction(nearest.u_mps, nearest.v_mps)
        speeds = [math.hypot(level.u_mps, level.v_mps)*MPS_TO_KNOTS for level in candidates]
        streets = inverse_direction is not None and 15 <= sum(speeds)/len(speeds) <= 25 and angular_difference(surface_direction, inverse_direction) < 20
    return ThermalResult(net, conservative, base, usable, depth, heat, velocity,
                         None if net is None else net >= 3.5, None if net is None else net >= 4.5,
                         None if base is None else base >= 4500, None if base is None else base >= 5000,
                         overdevelopment, streets, cumulus, True, tuple(notes))


@dataclass(frozen=True)
class WaveResult:
    site: str
    model: str
    score: float | None
    complete: bool
    components: Mapping[str, float]
    brunt_vaisala_n2: float | None
    scorer_lower_m2: float | None
    scorer_upper_m2: float | None
    uplift_850_knots: float | None
    uplift_700_knots: float | None
    wind_from_deg: float | None
    characteristic_scale_km: float | None
    notes: tuple[str, ...]


def evaluate_wave(profile: Profile, ridge: WaveRidge) -> WaveResult:
    notes = ['Heuristic wave potential; coarse vertical levels do not resolve rotor or wave crests',
             'Ridge geometry uses an unverified representative summit anchor']
    components = {}
    flow_levels = [l for l in (profile.level(925), profile.level(850)) if l and l.u_mps is not None]
    direction = None
    if flow_levels:
        flow = max(flow_levels, key=lambda level: math.hypot(level.u_mps, level.v_mps))
        direction = wind_direction(flow.u_mps, flow.v_mps)
        speed = math.hypot(flow.u_mps, flow.v_mps) * MPS_TO_KNOTS
        aligned = direction is not None and min(angular_difference(direction, az) for az in ridge.perpendicular_wind_azimuths_deg) <= 30
        components['cross_ridge_flow'] = 30.0 if speed >= 18 and aligned else 0.0
    low, high = profile.level(900), profile.level(500)
    if low and high and low.u_mps is not None and high.u_mps is not None:
        a, b = wind_direction(low.u_mps, low.v_mps), wind_direction(high.u_mps, high.v_mps)
        components['directional_shear'] = 20.0 if a is not None and b is not None and angular_difference(a, b) < 30 else 0.0
        if low.interpolated:
            notes.append('900 hPa wind interpolated in log-pressure using vector components')
    at850, at700 = profile.level(850), profile.level(700)
    n2 = lower_scorer = upper_scorer = scale = None
    if at850 and at700 and at700.height_m > at850.height_m:
        theta = (at850.theta_k + at700.theta_k) / 2
        n2 = G / theta * (at700.theta_k-at850.theta_k) / (at700.height_m-at850.height_m)
        components['static_stability'] = 20.0 if n2 >= 1e-5 else 0.0
    levels = [l for l in profile.levels if l.u_mps is not None and 500 <= l.pressure_hpa <= 1000]
    if len(levels) >= 4 and at850 and at700 and direction is not None:
        azimuth = min(ridge.perpendicular_wind_azimuths_deg, key=lambda az: angular_difference(direction, az))
        normal_u, normal_v = wind_components(1, azimuth)
        z = np.array([l.height_m for l in levels])
        theta = np.array([l.theta_k for l in levels])
        cross = np.array([l.u_mps*normal_u + l.v_mps*normal_v for l in levels])
        if np.all(cross > 3):
            n_squared = G / theta * np.gradient(theta, z, edge_order=2)
            curvature = np.gradient(np.gradient(cross, z, edge_order=2), z, edge_order=2)
            scorer = n_squared / cross**2 - curvature / cross
            lower_scorer = float(np.interp(at850.height_m, z, scorer))
            upper_scorer = float(np.interp(at700.height_m, z, scorer))
            components['scorer_decrease'] = 20.0 if lower_scorer > upper_scorer and lower_scorer > 0 else 0.0
            if lower_scorer > 0:
                scale = 2 * math.pi / math.sqrt(lower_scorer) / 1000
        else:
            components['scorer_decrease'] = 0.0
            notes.append('Weak/reversing cross-ridge flow: possible critical level')
    w850, w700 = (None if at850 is None else at850.w_mps), (None if at700 is None else at700.w_mps)
    if w850 is not None and w700 is not None:
        components['model_ascent'] = 10.0 if max(w850, w700) > 0 else 0.0
    else:
        notes.append('Pressure-level vertical motion unavailable; uplift confirmation missing')
    complete = len(components) == 5
    if not complete:
        notes.append('Incomplete evidence: provisional score cannot trigger a wave alert')
    score = sum(components.values()) if components else None
    if score is not None and any(components.get(name) == 0 for name in ('cross_ridge_flow','directional_shear','static_stability','scorer_decrease')):
        score = min(score,70.0)  # A failed core criterion cannot reach the alert threshold.
    return WaveResult(ridge.point.key, profile.forecast.model, score,
                      complete, components, n2, lower_scorer, upper_scorer,
                      None if w850 is None else w850*MPS_TO_KNOTS,
                      None if w700 is None else w700*MPS_TO_KNOTS, direction, scale, tuple(notes))


def rank_wave_sites(results: Iterable[WaveResult]) -> tuple[WaveResult, ...]:
    return tuple(sorted(results, key=lambda r: (r.score is not None, r.complete, r.score or 0), reverse=True))


def maccready_speed(climb_mps: float, polar: Polar) -> tuple[float, bool]:
    """Solve v*s'(v)-s(v)=MC; clamp to the validated polar fit interval."""
    if not math.isfinite(climb_mps) or climb_mps < 0:
        raise ValueError('MacCready climb must be finite and nonnegative')
    optimum = math.sqrt((climb_mps + polar.c) / polar.a)
    bounded = min(polar.max_fit_speed_mps, max(polar.min_fit_speed_mps, optimum))
    return bounded, bounded != optimum


def cross_country_speed(climb_mps: float, bearing_deg: float, u_mps: float, v_mps: float,
                        polar: Polar) -> tuple[float, float, bool]:
    """Ideal cycle-average ground speed including drift in both climb and glide.

    Equivalent airspeed over a climb/glide cycle is v*c/(c+s(v)). Crab this
    average vector to cancel crosswind; add the along-track wind. No final glide,
    launch/landing margin, airspace diversion or flight-envelope approval implied.
    """
    if not all(math.isfinite(value) for value in (climb_mps, bearing_deg, u_mps, v_mps)):
        raise ValueError('Speed calculation needs finite inputs')
    airspeed, limited = maccready_speed(max(0, climb_mps), polar)
    if climb_mps <= 0:
        return 0.0, airspeed, limited
    equivalent = airspeed * climb_mps / (climb_mps + polar.sink_mps(airspeed))
    angle = math.radians(bearing_deg)
    along = u_mps*math.sin(angle) + v_mps*math.cos(angle)
    across = u_mps*math.cos(angle) - v_mps*math.sin(angle)
    if abs(across) >= equivalent:
        return 0.0, airspeed, limited
    return max(0, math.sqrt(equivalent**2 - across**2) + along) * 3.6, airspeed, limited


def select_forecast(forecasts: Iterable[Forecast], location_key: str, when: datetime,
                    preference: tuple[str, ...] = ('ukmo_uk_deterministic_2km', 'icon_eu', 'ecmwf_ifs025')) -> Forecast | None:
    matching = [f for f in forecasts if f.location.key in {location_key, 'region:'+location_key}]
    for model in preference:
        for forecast in matching:
            if forecast.model != model:
                continue
            try:
                if forecast.at(when).get('temperature_2m') is not None:
                    return forecast
            except KeyError:
                continue
    return None


@dataclass(frozen=True)
class LegHour:
    valid_at: datetime
    leg: str
    groundspeed_kmh: float | None
    cruise_airspeed_kmh: float | None
    net_climb_knots: float | None
    models: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class TaskDay:
    route: str
    date: date
    route_distance_km: float
    hourly_legs: tuple[LegHour, ...]
    viability: Mapping[int, str]
    route_completion: str
    notes: tuple[str, ...]


def evaluate_task_day(route: Route, forecasts: Iterable[Forecast], day: date,
                      loading: str = 'dry', preference: tuple[str, ...] = ('ukmo_uk_deterministic_2km', 'icon_eu', 'ecmwf_ifs025')) -> TaskDay:
    forecasts = tuple(forecasts)
    polar = DUO_DISCUS_POLARS[loading]
    lengths = [distance_bearing(a, b)[0] for a, b in route.legs]
    hours, speeds = [], []
    for hour in range(10, 19):
        when = utc_datetime(datetime.combine(day, time(hour), LOCAL_TIMEZONE))
        this_hour = []
        for a, b in route.legs:
            models, thermals, winds, notes = [], [], [], []
            for point in (a, b):
                forecast = select_forecast(forecasts, point.key, when, preference)
                if forecast is None:
                    notes.append(f'{point.name}: missing forecast')
                    break
                profile = Profile.from_forecast(forecast, when)
                thermal = evaluate_thermals(profile)
                # Use conservative moisture sensitivity for task capacity as well.
                level = profile.level(925) or profile.level(850)
                if thermal.conservative_climb_knots is None or level is None or level.u_mps is None:
                    notes.append(f'{point.name}: missing climb or cruise wind')
                    break
                thermals.append(thermal.conservative_climb_knots)
                winds.append((level.u_mps, level.v_mps))
                models.append(forecast.model)
            ground = cruise = climb = None
            if len(thermals) == 2:
                climb = min(thermals)
                _, bearing = distance_bearing(a, b)
                ground, cruise_mps, limited = cross_country_speed(climb/MPS_TO_KNOTS, bearing,
                    sum(w[0] for w in winds)/2, sum(w[1] for w in winds)/2, polar)
                cruise = cruise_mps * 3.6
                if limited:
                    notes.append('MacCready optimum exceeds fitted polar interval; speed bounded')
            hours.append(LegHour(when, f'{a.name} → {b.name}', ground, cruise, climb, tuple(models), tuple(notes)))
            this_hour.append(ground)
        speeds.append(this_hour)

    def supported(target_km):
        scaled = [length * target_km/sum(lengths) for length in lengths]
        unknown = False
        # Try launches at each hourly boundary; unsupported gaps end a flight.
        for launch in range(8):
            leg, remaining = 0, scaled[0]
            for row in speeds[launch:8]:  # 10:00–18:00 is eight intervals, not nine.
                duration = 1.0
                while duration > 1e-9 and leg < len(scaled):
                    speed = row[leg]
                    if speed is None:
                        unknown = True
                        break
                    if speed <= 0:
                        break
                    travel = min(duration, remaining / speed)
                    duration -= travel
                    remaining -= speed*travel
                    if remaining < 1e-7:
                        leg += 1
                        if leg == len(scaled):
                            return 'supported'
                        remaining = scaled[leg]
                if leg < len(scaled) and (row[leg] is None or row[leg] <= 0):
                    break
        return 'insufficient_data' if unknown else 'not_supported'

    return TaskDay(route.key, day, sum(lengths), tuple(hours),
                   {km: supported(km) for km in TASK_DISTANCES_KM}, supported(sum(lengths)),
                   ('Idealized continuous soaring capacity, not a terrain/airspace-cleared flight plan',
                    'Distance variants scale the route legs proportionally; 10:00–18:00 Europe/London',
                    'Endpoint weather represents each leg; unsampled mid-leg weather remains uncertain'))


@dataclass(frozen=True)
class ConvergenceCell:
    row: int
    column: int
    latitude: float
    longitude: float
    surface_s1: float
    upper_s1: float | None
    dewpoint_gradient_k_per_100km: float
    classification: str
    region: str


@dataclass(frozen=True)
class ConvergenceResult:
    cells: tuple[ConvergenceCell, ...]
    valid_surface_cells: int
    total_cells: int
    model: str
    valid_at: datetime
    spacing_km: float
    notes: tuple[str, ...]


def convergence_fields(latitudes: np.ndarray, longitudes: np.ndarray,
                       u: np.ndarray, v: np.ndarray, dewpoint: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Spherical horizontal divergence and dewpoint gradient with metric distances.

    Axes must increase south-to-north and west-to-east. NaNs propagate through
    finite-difference stencils; missing wind is never interpreted as zero.
    """
    latitudes, longitudes = np.asarray(latitudes, dtype=float), np.asarray(longitudes, dtype=float)
    if latitudes.ndim != 1 or longitudes.ndim != 1 or min(len(latitudes), len(longitudes)) < 3:
        raise ValueError('Convergence needs at least a 3x3 rectilinear grid')
    if not np.all(np.diff(latitudes) > 0) or not np.all(np.diff(longitudes) > 0):
        raise ValueError('Grid axes must be strictly increasing')
    shape = (len(latitudes), len(longitudes))
    u, v, dewpoint = (np.asarray(a, dtype=float) for a in (u, v, dewpoint))
    if any(a.shape != shape for a in (u, v, dewpoint)):
        raise ValueError('Field shape does not match grid coordinates')
    phi, lam = np.radians(latitudes), np.radians(longitudes)
    cosine = np.cos(phi)[:, None]
    if np.any(cosine < .01):
        raise ValueError('Polar grids are not supported')
    divergence = (np.gradient(u, lam, axis=1, edge_order=2) +
                  np.gradient(v*cosine, phi, axis=0, edge_order=2)) / (EARTH_RADIUS_M*cosine)
    gradient_x = np.gradient(dewpoint, lam, axis=1, edge_order=2) / (EARTH_RADIUS_M*cosine)
    gradient_y = np.gradient(dewpoint, phi, axis=0, edge_order=2) / EARTH_RADIUS_M
    return -divergence, np.hypot(gradient_x, gradient_y) * 100_000


def convergence_region(latitude: float, longitude: float) -> str:
    if 51.5 <= latitude <= 53.6 and -3.7 <= longitude <= -2.1:
        return 'Welsh Marches'
    if 53.2 <= latitude <= 55.5 and -3.0 <= longitude <= -1.0:
        return 'Pennines'
    if 50.0 <= latitude <= 52.0 and -6.5 <= longitude <= 2.0:
        return 'Southern England'
    return 'England and Wales'


def detect_convergence(grid: tuple[tuple[GeoPoint, ...], ...], forecasts: Iterable[Forecast],
                       when: datetime, model: str, *, surface_threshold: float = 2e-5,
                       upper_threshold: float = 1e-5, dewpoint_threshold: float = 1.0,
                       land_mask: np.ndarray | None = None) -> ConvergenceResult:
    rows, columns = len(grid), len(grid[0])
    if any(len(row) != columns for row in grid):
        raise ValueError('Grid is not rectangular')
    latitude = np.array([row[0].latitude for row in grid])
    longitude = np.array([p.longitude for p in grid[0]])
    arrays = [np.full((rows, columns), np.nan) for _ in range(6)]
    u, v, up_u, up_v, dew, radiation = arrays
    available = {f.location.key: f for f in forecasts if f.model == model}
    for r, row in enumerate(grid):
        for c, point in enumerate(row):
            forecast = available.get(point.key)
            if forecast is None:
                continue
            try:
                values = forecast.at(when)
            except KeyError:
                continue
            speed, direction = values.get('wind_speed_10m'), values.get('wind_direction_10m')
            if speed is not None and direction is not None:
                u[r, c], v[r, c] = wind_components(speed, direction)
            if values.get('dew_point_2m') is not None:
                dew[r, c] = values['dew_point_2m']
            if values.get('shortwave_radiation') is not None:
                radiation[r, c] = values['shortwave_radiation']
            # Compact grid fetches retain heights and winds at 1000/950/925 hPa.
            pressure = 950
            speed, direction, height = (values.get(f'{name}_{pressure}hPa') for name in ('wind_speed','wind_direction','geopotential_height'))
            if speed is not None and direction is not None and height is not None and height > forecast.terrain_elevation_m:
                up_u[r, c], up_v[r, c] = wind_components(speed, direction)
            else:
                bounds = []
                for pressure in (1000, 925):
                    speed, direction, height = (values.get(f'{name}_{pressure}hPa') for name in ('wind_speed','wind_direction','geopotential_height'))
                    if speed is None or direction is None or height is None or height <= forecast.terrain_elevation_m:
                        break
                    bounds.append((*wind_components(speed, direction), height))
                if len(bounds) == 2:
                    weight = math.log(950/1000)/math.log(925/1000)
                    up_u[r, c], up_v[r, c] = (bounds[0][i]+weight*(bounds[1][i]-bounds[0][i]) for i in (0,1))
    surface, gradient = convergence_fields(latitude, longitude, u, v, dew)
    upper, _ = convergence_fields(latitude, longitude, up_u, up_v, dew)
    selected = (surface >= surface_threshold) & (gradient >= dewpoint_threshold)
    selected[[0,-1], :] = False
    selected[:, [0,-1]] = False  # Exclude one-sided boundary derivatives from alerts.
    if land_mask is not None:
        land_mask = np.asarray(land_mask, dtype=bool)
        if land_mask.shape != selected.shape:
            raise ValueError('Land mask shape mismatch')
    cells = []
    for r, c in zip(*np.where(selected)):
        deep = np.isfinite(upper[r,c]) and upper[r,c] >= upper_threshold
        coastal = land_mask is not None and np.any(land_mask[r-1:r+2,c-1:c+2]) and not np.all(land_mask[r-1:r+2,c-1:c+2])
        classification = 'deep / synoptic candidate' if deep else ('coastal sea-breeze candidate' if coastal and radiation[r,c] > 100 else 'surface convergence candidate')
        cells.append(ConvergenceCell(int(r), int(c), float(latitude[r]), float(longitude[c]), float(surface[r,c]),
                     float(upper[r,c]) if np.isfinite(upper[r,c]) else None, float(gradient[r,c]), classification,
                     convergence_region(float(latitude[r]), float(longitude[c]))))
    spacing = float(np.mean(np.diff(latitude))) * math.pi/180 * EARTH_RADIUS_M/1000
    return ConvergenceResult(tuple(cells), int(np.count_nonzero(np.isfinite(surface))), rows*columns,
                             model, utc_datetime(when), spacing,
                             ('Regional screening grid; narrow sea-breeze fronts can remain unresolved',
                              'Front type is a candidate classification, not a diagnosed dynamical cause',
                              '950 hPa wind may be log-pressure interpolated from above-terrain 1000/925 hPa winds'))
