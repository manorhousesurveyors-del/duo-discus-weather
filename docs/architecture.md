# Synchronous weather architecture — 8 November 2026

The dashboard completes weather requests before rendering its cards and map.
It has no weather recovery timer, periodic fragment, or automatic app rerun.
Only an explicit user action changes the Weather page. Animated wind and live
OGN graphics remain client-side features; they do not reload the weather iframe.

## Emergency diagnostic

The previous implementation combined three faults:

1. A two-second fragment compared background Future identities. Failed jobs
   acquired new identities on retry and triggered a full Streamlit rerun.
2. Each rerun explicitly emptied the page container, tearing down its map.
3. The 23-point regional request asked for 129 variables: 296.7 weighted calls
   against a 300/minute local budget. The 168-point grid required 2,167.2 more.
   Primary requests could repeatedly consume the allowance before donor data
   completed verification. Requiring every optional field overnight compounded
   this starvation. A test-only 10,000/minute budget had hidden the problem.

The synchronous path removes the polling/Future scheduler from dashboard
weather reads. Strict flight assessment remains separate from map availability.

## Data flow

`app.py` selects exactly one renderer in `pages/`. `weather_ui.py` and the
regional/grid adapters call `sync_weather.py`. Its `cached_forecasts` function
uses `@st.cache_data(ttl=1800, show_spinner=False)` and makes one multi-coordinate
GET per product for five calendar days. There is one attempt, a four-second
socket timeout, and no retry or pacing sleeps. The socket timeout is not a
promise about an external server's total response time.

The regional request contains 30 standard variables: the requested 21 surface
and wind fields plus temperature, geopotential height and humidity at 925, 850
and 700 hPa. These nine sounding fields support genuine inversion/moisture
checks; surface dewpoint alone cannot describe the upper atmosphere. The full
168-point UK/Ireland wind grid requests only 12 temperature, pressure and wind
fields. Together these consume 270.6 weighted calls, below the default 300/minute
budget, with two HTTP requests rather than dozens of full-sounding requests. When UKMO
omits late shortwave radiation, one additional request asks ECMWF for only
`temperature_2m,shortwave_radiation` at affected coordinates. This brings the
combined regional/support/grid cost to 293.6, still below 300. Completion preserves
native hours and records the donor on each repaired hour; support failures return
usable primary data outside the successful cache, with no automatic retry.

Coordinates, selected model, product and half-hour cycle identify the cache.
Selected date/hour, task and active page do not. The national grid is requested
only for relevant Weather map overlays; other pages reuse regional or explicitly
requested local forecasts. The grid adapter reuses its completed snapshot and
read-only hourly NumPy lookup. Its completed-Future wrappers exist only for API
compatibility and never schedule work.

## Missing fields and failures

- Parsing validates timestamps, units, coordinates and response ordering. Empty,
  malformed or short-horizon responses raise outside the successful cache.
- A missing optional field does not reject all locations or hide the map.
- `forecast_sanitizer.py` derives surface pressure from MSL pressure, elevation
  and temperature, and relative humidity using Magnus–Tetens. Wind interpolation
  uses vector components between available pressure levels.
- Missing mixing height first uses the available parcel sounding. Where needed,
  a conservative solar-energy estimate is explicitly labelled with its assumed
  stability. The LCL formula `125*(T-Td)` is not relabelled as measured mixing
  height. Native zeroes and provenance are preserved.
- Missing rain, radiation and wind are never invented. Task ratings continue to
  require adequate evidence. An estimated field is not an observation.
- A failed refresh can reuse a bounded process-local last successful forecast,
  retaining its original retrieval time and valid horizon. No request retries
  itself. The page reports the failure and offers explicit Refresh weather.
- A cold process without stored real data reports that condition honestly. It
  does not manufacture a synoptic forecast or label synthetic flying conditions
  as verified. The map and controls still work.
- Explicit Refresh clears both data and derived-grid caches, retaining the real
  last-good backup. Provider Retry-After cooldowns remain respected.

## Page and service isolation

The Weather map prepares an enabled point probe synchronously from cached
samples. Wave and task overlays likewise complete their cached pure calculations
in the selected page. Optional assistant jobs have an explicit Check result
button, never a timer. OGN remains independent of the weather engine, with real
traffic only. Saved tasks and user preferences survive the session migration;
obsolete asynchronous grid handles and documents are discarded on deployment.

`runtime_imports.py` refreshes already-loaded provider and consumer modules in
dependency order during Streamlit hot deployments. This prevents an old page
from retaining the removed pending-weather API.

## Verification

`tests/test_sync_weather.py` exercises the real default request budget, one-pass
model completion, success caching, HTTP 429/timeout handling and original-time
stored fallback. Compact fixtures return only the fields actually requested.

`scripts/run_architecture_checks.py` tests healthy, HTTP 429 and timeout sessions
in Chromium. It checks numeric headline metrics and five rated 8/8 day cards on
the first healthy render. Ten-second clean-map and wind-map observations count
full reruns, websocket rerun requests, HTTP calls, iframe replacement/navigation
and Leaflet identity/bounds changes. All must remain zero. It also checks black
streaming arrows, meteorological barbs, altitude inspection, lazy page navigation
and explicit Refresh after provider recovery. Test data is labelled synthetic
and confined to test fixtures.

Live checks in this environment retrieved the 23 regional points in 1.63 seconds
and the 168 wind-grid points in 1.37 seconds. Long Mynd had 8/8 local soaring hours
on each of the five days. These are measurements, not upstream availability
or Streamlit Cloud performance guarantees.

Final Chromium checks passed for healthy, HTTP 429 and timeout providers: six
10-second idle windows had zero automatic reruns, weather calls, iframe reloads
or map jumps. Warm server renders across seven pages measured 6–30 ms; probe
preparation measured 32 ms and wind control switches stayed below 3 ms. External
tiles are intentionally blocked in this deterministic test; the live HTTP checks
above are separate. Socket timeouts are per request: sequential products or
selective support can make a cold explicit interaction longer than four seconds.

A live UKMO check after selective support completed in 1.18 seconds with 8/8
local soaring hours on all five days. A live ECMWF check also covered all five
days; missing UKMO shortwave values were supplied by ECMWF with provenance.
