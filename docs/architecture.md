# Nine-anchor weather and live failover — 8 November 2026

The dashboard completes a synchronous weather pass before rendering its cards
and map. No weather timer, automatic rerun or provider cooldown locks the UI.
Refresh weather always permits a new provider attempt. Live OGN is independent.

## Root causes and corrections

The former 23-location regional request plus 168-location grid competed for a
weighted Open-Meteo allowance. Earlier versions also requested 129 variables at
each point and retried incomplete jobs through a two-second fragment. That
fragment restarted the application and removed its map container. Removing the
rerun loop stopped flashing, but the shared provider gate could still reject a
fresh request before any network call.

`sync_weather.SyncHttpClient` now inherits the plain JSON transport, with no
provider gate or cooldown state. It makes one attempt with a 1.25-second socket
timeout and no pacing/retry sleeps. It sends the identifying User-Agent
`DuoDiscusSoaringWeather/1.0 github.com/manorhousesurveyors-del/duo-discus-weather`.
No claim is made that either external provider can never rate-limit or fail.

## Forecast path

1. `weather_anchors.ANCHORS` defines exactly nine real sample locations: Long
   Mynd, Exeter, Cambridge, Denbigh, Sutton Bank, Borders, Aboyne, Belfast and
   Dublin. One Open-Meteo request fetches five days at these locations. It asks
   for 30 supported surface/wind/sounding variables, a weighted cost of 27 rather
   than the former 270.6. Missing essential columns may use one selective donor
   request, still limited to the same nine anchors.
2. HTTP 429, timeout, malformed data or unresolved essential gaps immediately
   try MET Norway's official Locationforecast compact endpoint. Nine point GETs
   run concurrently. `met_norway.fetch_met_forecasts` returns within a 1.5-second
   caller deadline; each socket uses a 1.25-second timeout. No same-provider retry
   loop or delayed recovery job is created.
3. Successful live anchor bundles enter `st.cache_data(ttl=1800,
   show_spinner=False)` and the atomic disk cache. Empty/failed HTTP responses
   are never cached as successful weather.
4. If both providers fail, use a valid stored live forecast with original
   provider, retrieval time, valid times and derivation metadata. Otherwise
   generate nine explicitly synthetic demonstration anchors in the same pass.
   A demonstration is never persisted as last-good weather.

`weather_anchors.sample_anchors` applies smooth radial-basis interpolation in
kilometres. Winds interpolate as vectors, including direction wraparound.
Scalar overshoot is bounded by the source range. Sparse columns use labelled
inverse-distance support; entirely missing columns remain unknown. These are
coarse regional estimates, not a replacement for high-resolution model grids.

Regional metrics, local inspections and the full 168-node UK/Ireland wind canvas
reuse the same anchor bundle. A Wind-mode click does not fetch 168 coordinates.
The displayed product, including an explicit demonstration, remains stable on
page/day/hour/mode changes until Refresh or the next half-hour cycle. This is a
labelled display snapshot, separate from the successful live-HTTP cache.

## MET Norway normalization and uncertainty

The adapter validates units, timestamps, coordinates and finite values. Native
surface temperature, humidity, wind, pressure and cloud layers are retained.
Missing dewpoint uses Magnus–Tetens with real temperature and humidity. Later
six-hour instant samples interpolate only between available timestamps. A
six-hour precipitation accumulation becomes a labelled six-hour period mean;
it is not presented as an exact observed hourly rate. Unbracketed earlier or
future hours are never extrapolated as live weather.

Compact Locationforecast does not supply the required pressure-level soundings.
Solar radiation, convective mixing and upper profiles are explicitly estimated.
They are not measured upper winds or a diagnosed inversion. A power-law profile
cannot reconstruct real geostrophic wind or mountain-wave structure from a
surface sample. The page, map callout, probe header and Wave view identify this
limitation. Known zero solar heating may give zero *convective* mixing in a
preview; that is not a measurement of the nocturnal atmospheric boundary layer.

`forecast_quality.py` separates display data from flight evidence. Operational
star ratings, task assessments and alert triggers exclude demonstrations and
MET surface-derived profiles. The Weather page may show numeric **Estimated**
or **Demo** daily preview cards, without operational stars or achievable-task
claims. Other pages show the same source banner; Wave evidence is labelled
Illustrative where applicable. Provenance survives spatial copies and JSON
roundtrips, including restored cache files.

## Disk and session lifecycle

`weather_disk_cache.py` writes `/tmp/duo_weather_cache.json`; deployments/tests
can override it with `DUO_WEATHER_CACHE_PATH`. Writes use a temporary file,
fsync and atomic replacement. The file is bounded to 8 MB and three selected
model bundles. Repeated hourly provenance labels are dictionary-encoded; a
nine-anchor MET bundle measured about 1.62 MB. Corrupt, future-dated, expired or
unreadable entries fail safely. A valid other-model entry retains its actual
provider identity when reused. `/tmp` survives session and process restarts on
the same filesystem, but is not guaranteed to survive container replacement.

The session migration drops old asynchronous/grid handles while preserving
saved tasks and preferences. Explicit Refresh clears live data, spatial product,
display-bundle and map caches, while retaining the disk backup. Hot-deployment
module refresh is applied only when the compatibility registry changes; normal
imports do not replace exception/dataclass identities underneath other callers.

## Verification and access

The regression suite covers nine-coordinate request limits, shared regional/wind
fetches, immediate 429/timeout failover, end-to-end synthetic-provider failover
under two seconds, source-preserving disk recovery, corrupt cache handling,
complete labelled offline displays, and rejection of display-only data by tasks
and alerts. MET tests cover interval rain, humidity-derived dewpoint, pressure
profiles, header requirements and the concurrent deadline.

Chromium checks healthy, 429, timeout and fully offline sessions. Each verifies
first-pass numeric metrics and five dated cards, all displayed wind levels,
black streaming arrows/barbs, provenance inside map callouts, unchanged network
counts on navigation, and ten-second idle windows with zero automatic reruns,
HTTP calls, iframe replacement/navigation or map movement. Fixtures block real
external tiles and use isolated disk caches. Timings are measured results,
not guarantees about remote API latency. Sequential primary/support/failover
work can take longer than a single socket timeout.

The managed coding environment initially blocked `api.met.no`. That hostname
was added to the network configuration draft; Review/Save and Publish apply it.
This environment setting is separate from Streamlit Cloud's outbound access.
Live MET verification requires the policy to be applied; realistic provider
fixtures exercise the complete failure and parsing paths independently.

Final verification: all 377 automated tests passed. Four Chromium scenarios
(healthy, 429, timeout and both providers offline) passed, including eight
10-second idle checks with zero automatic activity. Warm page rendering measured
7–100 ms, probe preparation32–50 ms, and wind controls below3 ms. A live
Open-Meteo request fetched all nine anchors in1.71 seconds in this environment.
Late-day MET cards use known remaining-hour surface temperatures when the
morning soaring window lies outside the returned data; they do not invent
historical thermals.
