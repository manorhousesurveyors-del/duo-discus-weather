"""Bounded best-effort weather reads for the short-lived alert process.

All requests share one wall-clock budget. Daemon workers never write state or
send notifications; a slow DNS lookup/body cannot hold interpreter shutdown.
Only results completed before the deadline enter the immutable forecast batch.
"""
from dataclasses import replace
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic

from config import HOURLY_VARIABLES, Settings
from data_ingestion import FetchFailure, ForecastBatch, IngestionError, JsonHttpClient, OpenMeteoClient

FETCH_BUDGET_SECONDS = 12.0
REQUEST_TIMEOUT_SECONDS = 3.5
MAX_WORKERS = 12


class WatchdogHttpClient(JsonHttpClient):
    """Single attempts: rate limits become reported gaps, never retry sleeps."""
    def __init__(self, **kwargs):
        settings = replace(Settings.from_env(), http_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
                           http_max_attempts=1, http_workers=MAX_WORKERS)
        super().__init__(settings, **kwargs)

    def _pace(self, host, cost=1.0):
        pass


def fetch_weather(groups, *, client=None, budget=FETCH_BUDGET_SECONDS):
    """Fetch (group, points, models, fields, chunk size) jobs concurrently.

Regional jobs go first; the optional convergence grid shares their deadline.
Ingestion errors/timeouts are warnings. Programming errors still propagate.
"""
    if not 0 < budget <= FETCH_BUDGET_SECONDS:
        raise ValueError('Weather deadline must be positive and at most 12 seconds')
    groups = tuple(groups)
    if client is None:
        http = WatchdogHttpClient()
        client = OpenMeteoClient(http.settings, http=http)
    jobs, pending, finished, stopped = [], Queue(), Queue(), Event()
    for group, points, models, fields, size in groups:
        points, fields = tuple(points), tuple(fields)
        if not 1 <= size <= 50 or not fields or not set(fields) <= set(HOURLY_VARIABLES):
            raise ValueError('Invalid watchdog batch size or weather fields')
        if len(points) > client.settings.max_grid_points or len({p.key for p in points}) != len(points):
            raise ValueError('Invalid watchdog point budget or duplicate location')
        for model in models:
            for offset in range(0, len(points), size):
                jobs.append((group, points[offset:offset+size], model, fields))
                pending.put(len(jobs)-1)
    deadline = monotonic() + budget

    def worker():
        while not stopped.is_set() and monotonic() < deadline:
            try:
                index = pending.get_nowait()
            except Empty:
                return
            _, points, model, fields = jobs[index]
            try:
                good, bad = client._fetch_chunk(points, model, fields)
                result = (good, bad)
            except (IngestionError, OSError) as exc:
                result = ((), tuple(FetchFailure(p.key, model.api_name,
                          f'Weather request unavailable ({type(exc).__name__}): {exc}') for p in points))
            except Exception as exc:
                result = exc
            finished.put((index, result))

    for _ in range(min(MAX_WORKERS, len(jobs))):
        Thread(target=worker, daemon=True, name='watchdog-weather').start()
    results = {}
    try:
        while len(results) < len(jobs):
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            try:
                index, result = finished.get(timeout=remaining)
            except Empty:
                break
            if isinstance(result, Exception):
                raise result
            results[index] = result
    finally:
        stopped.set()
    grouped = {group: ([], []) for group, *_ in groups}
    for index, (group, points, model, _) in enumerate(jobs):
        good, bad = results.get(index, ((), tuple(FetchFailure(p.key, model.api_name,
            f'Weather deadline reached ({budget:g}s); no retry this run') for p in points)))
        grouped[group][0].extend(good)
        grouped[group][1].extend(bad)
    return {group: ForecastBatch(tuple(sorted(good, key=lambda f: (f.location.key, f.model))),
                                tuple(sorted(bad, key=lambda f: (f.target, f.provider))))
            for group, (good, bad) in grouped.items()}
