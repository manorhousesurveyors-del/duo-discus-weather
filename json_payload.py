"""Strict, script-safe JSON shared by every browser weather payload."""
import json
import math
from collections.abc import Mapping
from datetime import date, datetime
import numpy as np


def finite_data(value):
    if isinstance(value, np.ndarray):
        return finite_data(value.tolist())
    if isinstance(value, np.generic):
        return finite_data(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): finite_data(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_data(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def browser_json(value):
    return json.dumps(finite_data(value), separators=(',', ':'), allow_nan=False).replace('<', '\\u003c')
