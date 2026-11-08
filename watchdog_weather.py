"""Compatibility API for the watchdog; scheduling lives in weather_engine."""
from weather_engine import (FETCH_BUDGET_SECONDS, REQUEST_TIMEOUT_SECONDS, MAX_WORKERS,
                            ForecastHttpClient as WatchdogHttpClient, fetch_groups as fetch_weather)
