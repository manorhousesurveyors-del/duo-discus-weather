"""Atomic, bounded persistence of real forecasts across Streamlit sessions.

Demonstration weather is never persisted as last-good data. MET Norway's real
surface forecast is permitted, retaining all estimated-profile provenance.
"""
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from threading import RLock
import tempfile

from config import FORECAST_MODELS
from data_ingestion import _json_default, forecast_from_snapshot, utc_datetime
from forecast_quality import is_demo

MAX_BYTES = 8 * 1024 * 1024
MAX_BUNDLES = 3
SCHEMA_VERSION = 1
_lock = RLock()


def _path(path=None):
    return Path(path if path is not None else os.environ.get('DUO_WEATHER_CACHE_PATH', '/tmp/duo_weather_cache.json'))


def _read(path):
    try:
        with path.open('rb') as source:
            raw = source.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:return {}
        payload = json.loads(raw)
        if not isinstance(payload,dict) or payload.get('schema_version')!=SCHEMA_VERSION:
            return {}
        bundles = payload.get('bundles')
        if not isinstance(bundles,dict) or len(bundles)>MAX_BUNDLES:
            return {}
        if any(key not in FORECAST_MODELS or not isinstance(value,dict) or
               not isinstance(value.get('forecasts'),list) or not isinstance(value.get('written_at'),str)
               for key,value in bundles.items()):
            return {}
        return bundles
    except (OSError,ValueError,TypeError):
        return {}


def _compact(records):
    """Store repeated hourly provenance once; snapshots expand before validation."""
    labels = sorted({origin for record in records for series in record.get('field_sources',{}).values()
                     for origin in series if origin is not None})
    lookup = {label:index for index,label in enumerate(labels)}
    encoded = [{**record,'field_sources':{key:[None if origin is None else lookup[origin] for origin in series]
                for key,series in record.get('field_sources',{}).items()}} for record in records]
    return encoded, labels


def _expand(record,bundle):
    labels = bundle.get('provenance_labels')
    if labels is None:return record  # Original schema1 snapshots remain readable.
    if not isinstance(labels,list) or any(not isinstance(label,str) for label in labels):
        raise ValueError('Invalid cached provenance dictionary')
    def label(value):
        if value is None:return None
        if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<len(labels):
            raise ValueError('Invalid cached provenance reference')
        return labels[value]
    return {**record,'field_sources':{key:[label(origin) for origin in series]
            for key,series in record.get('field_sources',{}).items()}}


def save_live(forecasts, model_key, path=None) -> bool:
    """Write real records atomically; an unusable directory never breaks the UI."""
    if model_key not in FORECAST_MODELS:return False
    try:
        forecasts = tuple(f for f in forecasts if not is_demo(f))
        if not forecasts:return False
        # Validate the same schema on write and read, retaining true provider
        # identity even when the bundle belongs to a different dropdown model.
        records = [asdict(f) for f in forecasts]
        serialised = json.loads(json.dumps(records,default=_json_default,allow_nan=False))
        for record in serialised:
            if is_demo(forecast_from_snapshot(record)):return False
        encoded, labels = _compact(serialised)
        target = _path(path)
        with _lock:
            bundles = _read(target)
            bundles[model_key] = {'written_at':datetime.now(UTC).isoformat(),'forecasts':encoded,
                                 'provenance_labels':labels}
            while len(bundles)>MAX_BUNDLES:
                oldest = min((key for key in bundles if key!=model_key), key=lambda key:bundles[key]['written_at'])
                bundles.pop(oldest)
            while True:
                data = json.dumps({'schema_version':SCHEMA_VERSION,'bundles':bundles},
                                  allow_nan=False,separators=(',',':')).encode('utf-8')
                if len(data)<=MAX_BYTES:break
                others = [key for key in bundles if key!=model_key]
                if not others:return False
                bundles.pop(min(others,key=lambda key:bundles[key]['written_at']))
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile('wb',dir=target.parent,prefix='.duo-weather-',delete=False) as output:
                    temporary = Path(output.name)
                    output.write(data);output.flush();os.fsync(output.fileno())
                os.replace(temporary,target)
            finally:
                if temporary is not None:temporary.unlink(missing_ok=True)
        return True
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RuntimeError):
        return False


def load_live(model_key, anchor, path=None) -> tuple:
    """Load valid, unexpired real data; never extend stored valid-time coverage.

    Prefer the requested dropdown bundle. If absent/expired, the newest valid
    other bundle may supply real data, keeping its actual model/source intact.
    """
    try:
        anchor = utc_datetime(anchor)
        with _lock:
            bundles = _read(_path(path))
        order = sorted(bundles,key=lambda key:bundles[key]['written_at'],reverse=True)
        if model_key in order:order.remove(model_key);order.insert(0,model_key)
        latest_allowed = datetime.now(UTC)+timedelta(minutes=5)
        for key in order:
            restored = []
            for record in bundles[key]['forecasts']:
                try:
                    forecast = forecast_from_snapshot(_expand(record,bundles[key]))
                    if is_demo(forecast) or forecast.fetched_at>latest_allowed:
                        continue
                    if (not forecast.times or forecast.times[0]>anchor or forecast.times[-1]<anchor
                            or forecast.available_through is None or forecast.available_through<anchor):
                        continue
                    restored.append(forecast)
                except (ValueError,TypeError,KeyError,AttributeError,RuntimeError):
                    continue
            if restored:return tuple(restored)
        return ()
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        return ()
