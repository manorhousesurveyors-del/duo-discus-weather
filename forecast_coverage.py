"""Explicit, time-aligned model support for dashboard and task forecasts.

Keep every supplied primary value. Fill gaps only at the same requested
coordinate and exact UTC hour, retaining donor identity for every value. CAPE
is optional evidence, never a prerequisite for dry thermal or day ratings.
"""
from dataclasses import replace
from datetime import timedelta

from config import HOURLY_VARIABLES
from soaring_physics import Profile, boundary_layer_height
from atmospheric_moisture import complete_moisture

COVERAGE_REVISION = 2
PARCEL_SOURCE = 'estimated dry-parcel mixing depth'


def complete_forecasts(forecasts, model):
    """Return the requested model with labelled support, without I/O or mutation.

    ECMWF supplies the longer-range support first, ICON the next choice. A
    wholly unavailable primary uses the first healthy donor. No temporal or
    spatial extrapolation, zero replacement, or invented favourable weather.
    """
    order = tuple(dict.fromkeys((model.api_name, 'ecmwf_ifs025', 'icon_eu',
                                'ukmo_seamless', 'ukmo_uk_deterministic_2km')))
    grouped = {}
    for f in forecasts:
        key = (f.location.key, f.location.latitude, f.location.longitude)
        grouped.setdefault(key, {})[f.model] = f
    results = []
    for key in sorted(grouped):
        choices = grouped[key]
        sources = [choices[name] for name in order if name in choices]
        if not sources:
            continue
        primary = sources[0]
        start = primary.times[0]
        # Provider responses include up to 24 past hours. Honour the selected
        # model's requested horizon even when a longer-range donor is present.
        end = start + timedelta(hours=model.horizon_hours + 24)
        available = {t for f in sources for t in f.times if start <= t <= end}
        times = tuple(start+timedelta(hours=i) for i in range(int((max(available)-start).total_seconds()//3600)+1))
        indices = [{t: i for i, t in enumerate(f.times)} for f in sources]
        values, units, provenance = {}, {}, {}
        for name in HOURLY_VARIABLES:
            unit = next((f.units.get(name) for f in sources
                         if any(v is not None for v in f.hourly.get(name, ()))), primary.units.get(name))
            series, origins = [], []
            for when in times:
                value = origin = None
                for f, lookup in zip(sources, indices):
                    index = lookup.get(when)
                    if index is None or name not in f.hourly or f.units.get(name) != unit:
                        continue  # Never combine omega units within one series.
                    value = f.hourly[name][index]
                    if value is not None:
                        existing = f.field_sources.get(name)
                        origin = (existing[index] if existing else None) or (f.model if f.model != model.api_name else None)
                        break
                series.append(value)
                origins.append(origin)
            values[name], units[name] = tuple(series), unit
            if any(origins):
                provenance[name] = tuple(origins)
        complete_moisture(values,units,provenance)
        resolved = replace(primary, model=model.api_name, times=times, hourly=values,
                           units=units, field_sources=provenance,
                           fetched_at=min(f.fetched_at for f in sources),
                           source_runs=tuple(sorted({run for f in sources for run in
                               (f.source_runs or ((f.model,f.fetched_at.isoformat()),))})),
                           requested_horizon_hours=model.horizon_hours)
        # Both UKMO and ECMWF can omit BLH entirely. Use the bracketed parcel
        # sounding after completion. Solar radiation drives the existing core
        # heat-flux calculation; do not double-count it as arbitrary extra lift.
        depth = list(values['boundary_layer_height'])
        origins = list(provenance.get('boundary_layer_height', (None,) * len(times)))
        for i, when in enumerate(times):
            if depth[i] is not None:
                continue
            try:
                estimate, _ = boundary_layer_height(Profile.from_forecast(resolved, when))
            except (ValueError, KeyError):
                continue
            if estimate is not None:
                depth[i], origins[i] = estimate, PARCEL_SOURCE
        values['boundary_layer_height'] = tuple(depth)
        if any(origins):
            provenance['boundary_layer_height'] = tuple(origins)
            units['boundary_layer_height'] = 'm'
        counts = {k: sum(v is None for v in series) for k, series in values.items()}
        missing = [k for k, n in counts.items() if n == len(times)]
        partial = [k for k, n in counts.items() if 0 < n < len(times)]
        notes = []
        if provenance:
            notes.append('Requested ' + model.api_name + '; gap support: ' +
                         ', '.join(sorted({s for v in provenance.values() for s in v if s})))
        if missing:
            notes.append('Unavailable variables: ' + ', '.join(missing))
        if partial:
            notes.append('Partially missing variables: ' + ', '.join(partial))
        results.append(replace(resolved, missing_counts=counts, warnings=tuple(notes)))
    return tuple(results)
