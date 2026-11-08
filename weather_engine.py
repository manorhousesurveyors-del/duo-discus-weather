"""Process-wide weather scheduling, admission control and verified-result cache.

No Streamlit imports, widgets, notification delivery or OGN dependencies. Network
work belongs to daemon workers. Readers never wait for a socket. Failed/partial
responses are transient results, never entries in the successful forecast cache.
Provider cooldown metadata is deliberately separate from cached weather.
"""
from collections import OrderedDict, deque
from concurrent.futures import Future
from dataclasses import dataclass, replace
from queue import Empty, Queue
from threading import Event, Lock, Thread
from time import monotonic
from urllib.parse import urlsplit

from config import FORECAST_MODELS, HOURLY_VARIABLES, Settings
from data_ingestion import FetchFailure, ForecastBatch, IngestionError, JsonHttpClient, ServiceError, OpenMeteoClient

ENGINE_REVISION = 1


class DeferredWeather(ServiceError):
    """Admission deferred; the UI can continue and the scheduler can retry later."""


class ProviderGate:
    def __init__(self, *, clock=monotonic, budget=300):
        self.clock, self.budget = clock, budget
        self.lock = Lock()
        self.costs = deque()
        self.until = 0.
        self.requests = self.deferred = self.rate_limits = 0

    def admit(self, cost):
        with self.lock:
            now = self.clock()
            while self.costs and self.costs[0][0] <= now-60:
                self.costs.popleft()
            if now < self.until:
                self.deferred += 1
                raise DeferredWeather('Weather provider cooling down; background recovery is scheduled',
                                      status=429, retry_after=self.until-now)
            if sum(c for _, c in self.costs)+cost > self.budget:
                self.deferred += 1
                delay = max(1., self.costs[0][0]+60-now) if self.costs else 60.
                raise DeferredWeather('Weather request budget reserved; background update queued', retry_after=delay)
            self.costs.append((now, cost)); self.requests += 1

    def failed(self, error):
        if isinstance(error, DeferredWeather):
            return
        with self.lock:
            delay = error.retry_after if getattr(error, 'retry_after', None) else (60. if getattr(error, 'status', None)==429 else 5.)
            self.until = max(self.until, self.clock()+delay)
            self.rate_limits += getattr(error, 'status', None)==429


class ForecastHttpClient(JsonHttpClient):
    """One 3.5-second attempt, no sleeps, no raw JSON cache, shared admission."""
    def __init__(self, *, gate=None, **kwargs):
        super().__init__(replace(Settings.from_env(), http_timeout_seconds=3.5,
                                 http_max_attempts=1, http_workers=3), **kwargs)
        self.gate = gate or ProviderGate(clock=self.monotonic, budget=self.settings.open_meteo_calls_per_minute)
        self._requests_lock=Lock();self._inflight={}

    def _pace(self, host, cost=1.):
        if host == 'api.open-meteo.com':
            self.gate.admit(cost)

    def get_json(self, endpoint, params, *, ttl_seconds=0):
        key=(endpoint,tuple(sorted(params.items())))
        with self._requests_lock:
            existing=self._inflight.get(key)
            if existing is None:self._inflight[key]=pending=Future()
        if existing is not None:return existing.result()
        try:
            # Validation/completion happens after parsing. Never memoize raw
            # empty arrays, missing model columns or HTTP failures as weather.
            response=super().get_json(endpoint, params, ttl_seconds=0)
        except IngestionError as error:
            if urlsplit(endpoint).hostname == 'api.open-meteo.com':
                self.gate.failed(error)
            pending.set_exception(error)
            raise
        except Exception as error:
            pending.set_exception(error);raise
        else:
            pending.set_result(response);return response
        finally:
            with self._requests_lock:self._inflight.pop(key,None)


@dataclass
class _Entry:
    future: Future | None = None
    good: object = None
    expires: float = 0.
    retry_at: float = 0.
    error: str = ''


class ResourceStore:
    """Bounded shared single-flight jobs with stale-while-revalidate semantics."""
    def __init__(self, *, clock=monotonic, workers=3, max_entries=128):
        self.clock, self.max_entries = clock, max_entries
        self.workers=workers
        self.lock = Lock(); self.entries = OrderedDict(); self.queue = Queue(maxsize=max_entries)
        for number in range(workers):
            Thread(target=self._worker, daemon=True, name=f'weather-engine-{number}').start()

    def _worker(self):
        while True:
            function = self.queue.get()
            try:
                if function is None:return
                function()
            finally:
                self.queue.task_done()

    def request(self, key, loader, *, accept=lambda value: value is not None, ttl=1800, retry=15):
        with self.lock:
            now = self.clock()
            entry = self.entries.setdefault(key, _Entry())
            self.entries.move_to_end(key)
            if entry.future is not None and (not entry.future.done() or now < max(entry.expires,entry.retry_at)):
                return entry.future
            future = entry.future = Future()
            def run():
                try:
                    value = loader()
                    valid = accept(value)
                except Exception as error:
                    with self.lock:
                        entry.error = str(error)
                        entry.retry_at = self.clock()+max(retry,getattr(error,'retry_after',0) or 0)
                    future.set_exception(error)
                else:
                    with self.lock:
                        if valid:
                            entry.good=value; entry.expires=self.clock()+ttl; entry.retry_at=0; entry.error=''
                        else:
                            entry.retry_at=self.clock()+retry; entry.error='Incomplete response; retry scheduled'
                    future.set_result(value)
            if self.queue.full():
                entry.retry_at=now+retry
                future.set_exception(DeferredWeather('Weather queue busy; retry scheduled',retry_after=retry))
            else:
                self.queue.put_nowait(run)
            # Never evict in-flight work: that would permit duplicate requests.
            for old in list(self.entries):
                if len(self.entries)<=self.max_entries:break
                if old!=key and self.entries[old].future.done():del self.entries[old]
            return future

    def previous(self, key):
        with self.lock:
            entry=self.entries.get(key)
            return entry.good if entry else None

    def clear(self):
        """Refresh invalidates successful TTLs, never in-flight work/cooldowns."""
        with self.lock:
            for entry in self.entries.values():entry.expires=0.

    def invalidate(self,key):
        """Refresh one successful resource without cancelling its live worker."""
        with self.lock:
            if key in self.entries:self.entries[key].expires=0.

    def close(self):
        for _ in range(self.workers):self.queue.put(None)


_lock=Lock()
_store=None
_transport=None
_products=None


def resources():
    global _store
    with _lock:
        if _store is None:_store=ResourceStore()
        return _store


def transport():
    global _transport
    with _lock:
        if _transport is None:_transport=ForecastHttpClient()
        return _transport


def products():
    """Pure chart work has independent workers and cannot delay provider I/O."""
    global _products
    with _lock:
        if _products is None:_products=ResourceStore(workers=2,max_entries=24)
        return _products


def complete_batch(batch, fields=None):
    """Cache admission contract: all requested points and required core hours.

    CAPE and optional upper levels are not required weather evidence. A compact
    request is validated only against its requested fields; zero is valid.
    """
    if not batch.forecasts:return False
    locations={f.location.key for f in batch.forecasts}
    if any(f.target not in locations for f in batch.failures):return False
    from forecast_sanitizer import has_core_coverage
    return all(has_core_coverage(f,fields) for f in batch.forecasts)


def prefer_verified(batch, points, model_key, fields=None):
    """Keep the last verified product during a failed/partial refresh."""
    previous=previous_forecasts(points,model_key,fields)
    if previous is not None and not complete_batch(batch,fields):
        return ForecastBatch(relabel(previous,points).forecasts,batch.failures)
    return relabel(batch,points)


def batch_key(points, model_key, fields=None):
    # Coordinates, not UI roles or widget day/hour, identify a provider request.
    return ('forecast',model_key,tuple(sorted((p.latitude,p.longitude) for p in points)),
            tuple(fields) if fields else None)


_aggregates=OrderedDict()
_aggregate_lock=Lock()
CHUNK_SIZE=8


def chunks(points):
    ordered=tuple(sorted(points,key=lambda p:(p.latitude,p.longitude)))
    return tuple(ordered[i:i+CHUNK_SIZE] for i in range(0,len(ordered),CHUNK_SIZE))


def previous_forecasts(points,model_key,fields=None):
    batches=[resources().previous(batch_key(group,model_key,fields)) for group in chunks(points)]
    good=tuple(f for batch in batches if batch for f in batch.forecasts)
    return ForecastBatch(good,()) if good else None


def request_forecasts(points, model_key, *, fields=None):
    """Schedule small resumable chunks, reusing every verified completed chunk.

    A national update can span a quota window without downloading its first
    successful chunks again. Aggregate futures hold no separate HTTP cache.
    """
    points=tuple(points)
    if not points:raise ValueError('At least one forecast coordinate is required')
    jobs=[]
    for group in chunks(points):
        key=batch_key(group,model_key,fields)
        def load(group=group):
            client=OpenMeteoClient(http=transport());model=FORECAST_MODELS[model_key]
            if fields is not None:
                return client.fetch_batched(group,(model,),variables=fields,batch_size=CHUNK_SIZE)
            return client.fetch_supported(group,model,batch_size=CHUNK_SIZE)
        jobs.append(resources().request(key,load,accept=lambda batch:complete_batch(batch,fields)))
    identity=tuple(jobs)
    with _aggregate_lock:
        if identity in _aggregates:return _aggregates[identity]
        result=Future();_aggregates[identity]=result
        for old in list(_aggregates):
            if len(_aggregates)<=128:break
            if old!=identity and _aggregates[old].done():del _aggregates[old]
    completion=Lock()
    def completed(_):
        with completion:
            if result.done() or not all(job.done() for job in jobs):return
            try:
                batches=[job.result() for job in jobs]
                result.set_result(ForecastBatch(tuple(f for batch in batches for f in batch.forecasts),
                                               tuple(f for batch in batches for f in batch.failures)))
            except Exception as error:result.set_exception(error)
    for job in jobs:job.add_done_callback(completed)
    return result


def read_forecasts(points, model_key, *, fields=None):
    """Immediate read used by UI adapters; exact original retrieval times remain."""
    points=tuple(points);future=request_forecasts(points,model_key,fields=fields)
    previous=previous_forecasts(points,model_key,fields)
    if future.done():
        try:
            current=future.result()
            if complete_batch(current,fields) or previous is None:return relabel(current,points)
        except IngestionError:
            if previous is None:return ForecastBatch((),())
    return relabel(previous,points) if previous else ForecastBatch((),())


def relabel(batch, points):
    """Location roles differ between task/probe/regional readers at one point."""
    lookup={(p.latitude,p.longitude):p for p in points}
    return ForecastBatch(tuple(replace(f,location=lookup.get((f.location.latitude,f.location.longitude),f.location))
                               for f in batch.forecasts),batch.failures)


# The CLI deadline policy uses this same transport and parser.
FETCH_BUDGET_SECONDS=12.0
REQUEST_TIMEOUT_SECONDS=3.5
MAX_WORKERS=12

def fetch_groups(groups, *, client=None, budget=FETCH_BUDGET_SECONDS):
    """Fetch (group, points, models, fields, chunk size) jobs concurrently.

Regional jobs go first; the optional convergence grid shares their deadline.
Ingestion errors/timeouts are warnings. Programming errors still propagate.
"""
    if not 0 < budget <= FETCH_BUDGET_SECONDS:
        raise ValueError('Weather deadline must be positive and at most 12 seconds')
    groups = tuple(groups)
    if client is None:
        http = transport()
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
