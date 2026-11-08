"""Explicit, time-aligned model support for dashboard and task forecasts.

Field completion preserves primary values at exact coordinates and UTC hours.
The regional coverage guard may select a complete donor profile when that
improves coverage. CAPE remains optional evidence for thermal/day ratings.
"""
from dataclasses import replace
from datetime import datetime, time, timedelta

from config import FORECAST_MODELS, HOURLY_VARIABLES, LOCAL_TIMEZONE
from soaring_physics import Profile, evaluate_thermals

COVERAGE_REVISION = 3
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
        resolved = replace(primary, model=model.api_name, times=times, hourly=values,
                           units=units, field_sources=provenance,
                           fetched_at=min(f.fetched_at for f in sources),
                           source_runs=tuple(sorted({run for f in sources for run in
                               (f.source_runs or ((f.model,f.fetched_at.isoformat()),))})),
                           requested_horizon_hours=model.horizon_hours)
        from forecast_sanitizer import sanitize
        resolved=sanitize(resolved)
        values,units,provenance=resolved.hourly,resolved.units,resolved.field_sources
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


def soaring_coverage(forecast, first_day, days=5):
    """Known hours, not flyable hours: use the rating AND classification fields.

    Zero climb, rain and poor days are valid evidence. CAPE is optional. Do not
    replace bad flying weather simply because a different model looks better.
    """
    coverage = []
    for offset in range(days):
        known = 0
        for hour in range(10, 18):
            when = datetime.combine(first_day+timedelta(days=offset), time(hour), LOCAL_TIMEZONE)
            try:
                profile = Profile.from_forecast(forecast, when)
            except KeyError:
                continue
            thermal = evaluate_thermals(profile)
            wind = any(level and level.u_mps is not None and level.v_mps is not None
                       for level in (profile.level(925), profile.level(850)))
            if (wind and thermal.conservative_climb_knots is not None
                    and thermal.usable_top_ft_amsl is not None and thermal.overdevelopment is not None
                    and profile.values.get('precipitation') is not None):
                known += 1
        coverage.append(known)
    return tuple(coverage)


def complete_regional_forecasts(forecasts, model, first_day):
    """Complete fields before validation, then prefer a usable donor sounding.

    If hourly merging still leaves a day below 8/8, compare ICON/ECMWF profiles
    at that exact requested coordinate. Replace the whole site's profile only
    when coverage improves, retaining donor identity and internally consistent
    terrain/units. This never fabricates weather during a total provider outage.
    """
    forecasts = tuple(forecasts)
    result = []
    for selected in complete_forecasts(forecasts, model):
        coverage = soaring_coverage(selected, first_day)
        score = (sum(n == 8 for n in coverage), sum(coverage))
        best = selected
        if min(coverage) < 8:
            peers = tuple(f for f in forecasts if f.location == selected.location)
            for key in ('icon_eu', 'ifs025'):
                donor = FORECAST_MODELS[key]
                if donor.api_name == model.api_name:
                    continue
                candidates = complete_forecasts(peers, donor)
                if not candidates:
                    continue
                candidate = candidates[0]
                other = soaring_coverage(candidate, first_day)
                other_score = (sum(n == 8 for n in other), sum(other))
                # Never repair one date by removing valid coverage from another.
                if other_score > score and all(a >= b for a, b in zip(other, coverage)):
                    best, coverage, score = candidate, other, other_score
                if min(coverage) == 8:
                    break
        if best is not selected:
            supplied_by = best.model
            best = complete_forecasts((best,), model)[0]
            best = replace(best, warnings=best.warnings+(
                f'Coverage fallback to {supplied_by}: a more complete five-day sounding replaced the unusable primary profile.',))
        result.append(best)
    return tuple(result)
