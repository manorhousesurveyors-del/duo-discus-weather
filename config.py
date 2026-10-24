"""Configuration and reference data for the original Duo Discus G-CKML.

Coordinates use WGS84; wind azimuths are meteorological *from* directions.
Forecast calculations use SI internally, UTC for storage, and Europe/London
for display (BST in summer, GMT in winter). Importing this module has no I/O.
See docs/data-sources.md for provenance and the limits of the reference data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
import math
import os
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_TIMEZONE = ZoneInfo("Europe/London")
STORAGE_TIMEZONE = UTC
METRES_TO_FEET = 1 / 0.3048
MPS_TO_KNOTS = 3600 / 1852
AIRCRAFT_REGISTRATION = "G-CKML"
AIRCRAFT_MODEL = "Schempp-Hirth Duo Discus (original)"
TASK_DISTANCES_KM = (250, 300, 500, 750)
CIRCLING_SINK_KNOTS = 1.4  # Rounded dry reference; calculations use the polar below.
TASK_LOCAL_HOURS = tuple(range(10, 19))
DASHBOARD_LOCAL_HOURS = tuple(range(9, 20))
ALERT_HORIZONS_DAYS = (5, 2)
ALERT_INTERVAL_HOURS = 6
DEFAULT_EMAIL_RECIPIENT = "manorhousesurveyors@gmail.com"
DEFAULT_WHATSAPP_RECIPIENT = "+447976125887"
SMTP_HOST = "smtp.gmail.com"
SMTP_SSL_PORT = 465


@dataclass(frozen=True, slots=True)
class GeoPoint:
    key: str
    name: str
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.name.strip():
            raise ValueError("A location needs a nonempty key and name")
        if not math.isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("Latitude must be finite and between -90 and 90")
        if not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("Longitude must be finite and between -180 and 180")


MONITORING_ZONES: Mapping[str, GeoPoint] = MappingProxyType({
    "shropshire": GeoPoint("shropshire", "Shropshire / Long Mynd", 52.518, -2.880),
    "snowdonia": GeoPoint("snowdonia", "Snowdonia", 53.068, -4.076),
    "herefordshire": GeoPoint("herefordshire", "Herefordshire", 52.240, -2.880),
    "south_wales": GeoPoint("south_wales", "South Wales / Talgarth", 51.977, -3.133),
    "oxfordshire": GeoPoint("oxfordshire", "Oxfordshire / Didcot", 51.606, -1.241),
    "cambridgeshire": GeoPoint("cambridgeshire", "Cambridgeshire", 52.205, 0.121),
    "lincolnshire": GeoPoint("lincolnshire", "Lincolnshire", 53.230, -0.540),
    "south_yorkshire": GeoPoint("south_yorkshire", "South Yorkshire", 53.522, -1.131),
    "humberside": GeoPoint("humberside", "Humberside / Goole", 53.704, -0.870),
})


@dataclass(frozen=True, slots=True)
class Route:
    key: str
    name: str
    waypoints: tuple[GeoPoint, ...]
    notes: str

    def __post_init__(self) -> None:
        if len(self.waypoints) < 2:
            raise ValueError("A route needs at least two waypoints")

    @property
    def legs(self) -> tuple[tuple[GeoPoint, GeoPoint], ...]:
        return tuple(zip(self.waypoints, self.waypoints[1:]))


ROUTES: Mapping[str, Route] = MappingProxyType({
    "A": Route("A", "Long Mynd – Goole – Didcot – Long Mynd", (
        MONITORING_ZONES["shropshire"], MONITORING_ZONES["humberside"],
        MONITORING_ZONES["oxfordshire"], MONITORING_ZONES["shropshire"],
    ), "Return to Long Mynd above Church Stretton, as specified in Module 3; "
       "town centres are planning waypoints, not declared turnpoints or landing sites."),
    "B": Route("B", "Snowdonia – South Wales", (
        MONITORING_ZONES["snowdonia"], MONITORING_ZONES["south_wales"],
    ), "One-way transit; no return leg is implied."),
})


@dataclass(frozen=True, slots=True)
class WaveRidge:
    point: GeoPoint
    representative_summit: str
    crest_elevation_ft: float
    perpendicular_wind_azimuths_deg: tuple[int, int]
    survey_verified: bool = False
    provenance: str = (
        "Approximate representative summit coordinate/elevation; azimuths supplied "
        "by the project specification. Not a surveyed ridge line or wave validation."
    )

    def __post_init__(self) -> None:
        if not math.isfinite(self.crest_elevation_ft) or self.crest_elevation_ft <= 0:
            raise ValueError("Crest elevation must be positive and finite")
        a, b = self.perpendicular_wind_azimuths_deg
        if not (0 <= a < 360 and 0 <= b < 360 and (a - b) % 360 == 180):
            raise ValueError("Ridge normals must be reciprocal azimuths")


def _ridge(key: str, name: str, summit: str, lat: float, lon: float,
           elevation_m: float, azimuth: int) -> WaveRidge:
    return WaveRidge(GeoPoint(key, name, lat, lon), summit,
                     elevation_m * METRES_TO_FEET, (azimuth, (azimuth + 180) % 360))


# Broad mountain regions do not have one exact crest coordinate. These named
# summit anchors are explicit approximations, retained separately from airfields.
WAVE_RIDGES: Mapping[str, WaveRidge] = MappingProxyType({
    "long_mynd": _ridge("long_mynd", "Long Mynd", "Pole Bank", 52.5185, -2.8805, 516, 280),
    "snowdonia": _ridge("snowdonia", "Snowdonia", "Yr Wyddfa", 53.0685, -4.0763, 1085, 250),
    "denbigh": _ridge("denbigh", "Denbigh / Lleweni Parc · Vale of Clwyd", "Moel Famau / Clwydians", 53.154, -3.257, 555, 270),
    "cambrian": _ridge("cambrian", "Mid Wales / Cambrian Mountains", "Plynlimon", 52.468, -3.783, 752, 270),
    "berwyn": _ridge("berwyn", "Berwyn Range", "Cadair Berwyn", 52.8806, -3.3807, 832, 240),
    "black_mountains": _ridge("black_mountains", "Black Mountains", "Waun Fach", 51.9620, -3.1431, 811, 310),
    "malvern": _ridge("malvern", "Malvern Hills", "Worcestershire Beacon", 52.1044, -2.3394, 425, 270),
    "mam_tor": _ridge("mam_tor", "Peak District / Mam Tor", "Mam Tor", 53.3491, -1.8091, 517, 260),
    "yorkshire_dales": _ridge("yorkshire_dales", "Yorkshire Dales", "Ingleborough", 54.1663, -2.3977, 723, 250),
    "cross_fell": _ridge("cross_fell", "Pennines / Cross Fell", "Cross Fell", 54.7025, -2.4861, 893, 240),
    "lake_district": _ridge("lake_district", "Lake District", "Helvellyn", 54.5268, -3.0161, 950, 250),
    "cheviots": _ridge("cheviots", "Cheviots / Milfield", "The Cheviot", 55.4788, -2.1478, 815, 260),
    "portmoak": _ridge("portmoak", "Portmoak / Bishop Hill (Fife)", "Bishop Hill", 56.2180, -3.3330, 461, 230),
    "cairngorms": _ridge("cairngorms", "Cairngorms / Aboyne", "Lochnagar", 56.9590, -3.2450, 1155, 270),
})

ICAO_STATIONS = (
    "EGOS", "EGNR", "EGCV", "EGBB", "EGBJ", "EGBS", "EGOV", "EGFF",
    "EGVN", "EGTK", "EGSC", "EGTC", "EGNJ", "EGXW", "EGXC",
)


@dataclass(frozen=True, slots=True)
class Polar:
    """Positive-down sink s(v)=a*v²+b*v+c, with v and s both in m/s.

    Bounds cover the three-point fit, not the aircraft's operating envelope.
    Speed-to-fly, bank corrections and task calculations belong to Module 3.
    """

    wing_loading_kg_m2: float
    a: float
    b: float
    c: float
    min_fit_speed_mps: float
    max_fit_speed_mps: float
    source: str

    def sink_mps(self, airspeed_mps: float) -> float:
        if not math.isfinite(airspeed_mps) or not self.min_fit_speed_mps <= airspeed_mps <= self.max_fit_speed_mps:
            raise ValueError("Airspeed is outside the polar's supported fit interval")
        return self.a * airspeed_mps**2 + self.b * airspeed_mps + self.c


POLAR_REFERENCE_POINTS = ((103 / 3.6, 0.64), (152 / 3.6, 1.25), (200 / 3.6, 2.51))
POLAR_REFERENCE_LOADING_KG_M2 = 615 / 16.4  # XCSoar reference mass / area.
POLAR_SOURCE = (
    "https://github.com/XCSoar/XCSoar/blob/"
    "d15883efacf62cb5ff25792182a0c295c2b42299/src/Polar/PolarStore.cpp#L130"
)


def _scaled_polar(loading: float) -> Polar:
    # Independent Lagrange interpolation of the three published numerical points.
    a = b = c = 0.0
    for i, (v, sink) in enumerate(POLAR_REFERENCE_POINTS):
        x, y = [p[0] for j, p in enumerate(POLAR_REFERENCE_POINTS) if i != j]
        denominator = (v - x) * (v - y)
        a += sink / denominator
        b -= sink * (x + y) / denominator
        c += sink * x * y / denominator
    scale = math.sqrt(loading / POLAR_REFERENCE_LOADING_KG_M2)
    # s_loaded(v)=scale*s_reference(v/scale).
    return Polar(loading, a / scale, b, c * scale,
                 POLAR_REFERENCE_POINTS[0][0] * scale,
                 POLAR_REFERENCE_POINTS[-1][0] * scale, POLAR_SOURCE)


DUO_DISCUS_POLARS: Mapping[str, Polar] = MappingProxyType({
    "dry": _scaled_polar(34.0), "ballasted": _scaled_polar(43.0),
})

def duo_circling_sink_knots(loading='dry', bank_degrees=25.0):
    """Polar minimum within its fitted range, scaled for a coordinated bank.

    s_bank = s_straight * load_factor**1.5, with speed scaled by sqrt(load_factor).
    The default is a representative thermalling turn, not a measured aircraft value.
    """
    if not math.isfinite(bank_degrees) or not 0 <= bank_degrees <= 60:
        raise ValueError('Thermalling bank must be between 0 and 60 degrees')
    polar=DUO_DISCUS_POLARS[loading]
    speed=max(polar.min_fit_speed_mps,min(polar.max_fit_speed_mps,-polar.b/(2*polar.a)))
    return polar.sink_mps(speed)*MPS_TO_KNOTS/math.cos(math.radians(bank_degrees))**1.5


SURFACE_VARIABLES = (
    "temperature_2m", "dew_point_2m", "surface_pressure", "pressure_msl",
    "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "precipitation", "precipitation_probability", "shortwave_radiation",
    "direct_radiation", "cape", "lifted_index", "boundary_layer_height",
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "soil_moisture_0_to_7cm",
    "wind_speed_80m", "wind_direction_80m", "wind_speed_100m", "wind_direction_100m", "visibility",
)
PRESSURE_LEVELS_HPA = (1000, 950, 925, 850, 800, 700, 600, 500, 400, 300, 250, 200)
PRESSURE_VARIABLES = (
    "temperature", "relative_humidity", "wind_speed", "wind_direction",
    "geopotential_height", "vertical_velocity", "cloud_cover",
)
def forecast_variables():
    """Surface and pressure fields, including high cloud through 200 hPa."""
    return SURFACE_VARIABLES + tuple(
        f"{variable}_{level}hPa" for level in PRESSURE_LEVELS_HPA for variable in PRESSURE_VARIABLES)


HOURLY_VARIABLES = forecast_variables()
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
AWC_BASE_URL = "https://aviationweather.gov/api/data"


@dataclass(frozen=True, slots=True)
class ForecastModel:
    key: str
    api_name: str
    nominal_resolution_km: float
    horizon_hours: int
    notes: str


FORECAST_MODELS: Mapping[str, ForecastModel] = MappingProxyType({
    "ukmo": ForecastModel("ukmo", "ukmo_seamless", 2, 120,
        "UKV at UK points with global Met Office coverage beyond its range; effective resolution varies. "
        "Missing fields remain explicit; the probe can use a labelled ECMWF fallback."),
    "icon_eu": ForecastModel("icon_eu", "icon_eu", 7, 120,
        "120-hour lead on main runs; shorter intermediate runs. No pressure vertical velocity."),
    "ifs025": ForecastModel("ifs025", "ecmwf_ifs025", 25, 144,
        "0.25-degree grid (km size varies with latitude). No native 950 hPa level; "
        "vertical velocity is served in m/s. Hourly API output can be interpolated."),
})


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path = PROJECT_ROOT / "var" / "duo_discus.sqlite3"
    http_timeout_seconds: float = 20.0
    http_max_attempts: int = 3
    http_workers: int = 3
    http_min_interval_seconds: float = 1.2
    open_meteo_calls_per_minute: int = 300
    forecast_cache_seconds: float = 900.0
    aviation_cache_seconds: float = 60.0
    metar_max_age_minutes: int = 90
    max_grid_points: int = 500

    def __post_init__(self) -> None:
        for name in ("http_timeout_seconds", "http_min_interval_seconds", "forecast_cache_seconds", "aviation_cache_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        for name, maximum in (("http_max_attempts", 5), ("http_workers", 16),
                              ("metar_max_age_minutes", 1440), ("max_grid_points", 10000),
                              ("open_meteo_calls_per_minute", 600)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer in 1..{maximum}")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Read non-secret settings explicitly; never load or log credentials."""
        source = os.environ if environ is None else environ
        defaults = cls()
        values: dict[str, object] = {}
        for name in cls.__dataclass_fields__:
            raw = source.get("DUO_" + name.upper())
            if raw is None:
                continue
            default = getattr(defaults, name)
            try:
                values[name] = Path(raw).expanduser() if isinstance(default, Path) else type(default)(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid DUO_{name.upper()}") from exc
        return cls(**values)


def forecast_locations() -> tuple[GeoPoint, ...]:
    """All region and ridge anchors, namespaced to avoid duplicate dictionary keys."""
    regions = tuple(GeoPoint(f"region:{p.key}", p.name, p.latitude, p.longitude)
                    for p in MONITORING_ZONES.values())
    ridges = tuple(GeoPoint(f"ridge:{r.point.key}", r.point.name, r.point.latitude, r.point.longitude)
                   for r in WAVE_RIDGES.values())
    return regions + ridges


def convergence_grid(*, south: float = 50.0, north: float = 55.8,
                     west: float = -6.5, east: float = 2.0,
                     step_degrees: float = 0.5, max_points: int = 500) -> tuple[tuple[GeoPoint, ...], ...]:
    """South-to-north rows, west-to-east columns, including coastal sea cells.

    No land mask or convergence calculation is applied here. Module 3 must use
    latitude-dependent physical distances for derivatives, not degree spacing.
    """
    GeoPoint("sw", "South-west", south, west)
    GeoPoint("ne", "North-east", north, east)
    if south >= north or west >= east or not math.isfinite(step_degrees) or step_degrees <= 0:
        raise ValueError("Grid needs increasing bounds and a positive finite step")
    rows = math.floor((north - south) / step_degrees + 1e-9) + 1
    columns = math.floor((east - west) / step_degrees + 1e-9) + 1
    if rows < 2 or columns < 2 or rows * columns > max_points:
        raise ValueError("Grid must have at least 2x2 points and respect the request budget")
    return tuple(tuple(GeoPoint(f"grid:{row}:{column}", f"Grid {row},{column}",
                               round(south + row * step_degrees, 8), round(west + column * step_degrees, 8))
                       for column in range(columns)) for row in range(rows))
