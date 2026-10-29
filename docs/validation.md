# Validation record

The first section records Modules 1–2. The completed Modules 3–5 checks and
updated cloud setup are recorded below it.

Validated on 23 October 2026 with Python 3.12.14 in the cloud workspace.

## Offline checks

`python3 -m unittest discover -s tests -v`: **50 tests passed**, none skipped.

The suite covers polar scaling against the attributed three-point reference,
London daylight-saving transitions, grid bounds, SQLite persistence and schema
checks, concurrent delivery claims, safe retry/reconciliation transitions,
historical profile round-trips, TLS enforcement, HTTP retry/cooldown/cache logic,
forecast units and missingness, wind vectors, visibility qualifiers, METAR age,
conditional TAF groups and atomic JSON output.

The default local database was initialized without adding any alerts or reference
profiles. SQLite `PRAGMA integrity_check` returned `ok`; WAL mode was verified.
The CLI help command and Python compilation also passed.

## Live checks

Command run from `/workspace/duo-discus-weather`:

```bash
python3 data_ingestion.py --mode all --all-locations --model all --output var/ingestion.json > var/ingestion-summary.json
```

The command completed with **exit code 0**. Forecast retrievals occurred between
19:58:37 and 19:59:51 UTC (20:58–20:59 BST) on 23 October 2026.

| Product | Result |
| --- | --- |
| UKMO UK 2 km | 21 locations successfully parsed, 49 hourly slots each |
| ICON-EU | 21 locations successfully parsed, 121 hourly slots each |
| ECMWF IFS 0.25° | 21 locations successfully parsed, 145 hourly slots each |
| Total forecasts | **63 successful, 0 failed** |
| METAR | **10 reports**, no stale reports, 0 parser/request failures |
| TAF | **12 forecasts**, 0 parser/request failures |

Each forecast model was checked at all nine regional coordinates and all twelve
ridge anchors. METAR/TAF requests included all 15 configured ICAO stations.
No METAR was returned for EGCV, EGBJ, EGBS, EGSC or EGTC; no TAF was returned for
EGCV, EGBS or EGVN. These are observations from this poll, not claims about future
station availability or operating status. Missing reports are exposed in the
normalized response.

The first aviation run exposed empty-string visibility in EGOS, EGTC and EGXW
conditional TAF groups. The parser was corrected to retain this as unspecified,
a regression test was added, and the complete live run above passed afterward.

## Data limitations confirmed by the live run

- Boundary layer height and lifted index were unavailable in **all three models**.
- UKMO and ICON-EU lacked pressure-level vertical velocity and the requested
  0–7 cm soil moisture field.
- ECMWF lacked all requested 950 hPa variables. Its available vertical velocity
  fields were returned in **m/s**.
- ICON-EU had null values at the end of all 21 requested windows: the current
  model run did not cover the entire rolling 120-hour horizon. The parser exposed
  incomplete coverage and preserved nulls. UKMO and ECMWF surface temperatures
  reached their requested final slots in this run.

These are provider/model limitations, not failed requests. They are material to
Module 3: boundary layer estimates, missing pressure levels and model coverage
need explicit methods or a separately attributed additional data source. This
implementation does not fabricate them or hide gaps by blending models.

Ridge coordinates remain approximate, unverified summit anchors. Polar
coefficients are derived from the attributed XCSoar reference and the requested
loadings, not an aircraft-specific measurement. See [data sources](data-sources.md).

## Reusable cloud configuration

The draft save was confirmed for the custom HTTPS domain additions
`api.open-meteo.com`, `aviationweather.gov` and `open-meteo.com`, preserving the
existing package-manager network preset. Startup instructions were also saved
with the repository directory, standard-library workflow, test and live-read
commands, data-quality checks and the instruction to use the existing checkout.
No install script or credentials were required. Network access worked during the
final live run; this report does not establish that a new environment version
has been published or that a fresh task has been restored from it.

Review and save the draft in environment settings, then publish the environment
to retain the reusable configuration. Runtime snapshots under `var/` are ignored
by Git and are time-specific; refresh them before any subsequent analysis.

## Modules 3–5: completed validation

Validated on 23 October 2026 using Python 3.12.14 and an isolated virtual
environment (`include-system-site-packages = false`). Installing the pinned
requirements succeeded; `pip check` reported no broken requirements.

`python -m unittest discover -s tests -v`: **87 tests passed**, none skipped.
The added coverage includes pressure-velocity conversion, vector interpolation,
below-terrain exclusion, parcel brackets, wet-soil sensitivity, thermal/cloud
thresholds, wave evidence gating, analytic MacCready optima, wind corrections,
eight-hour route integration, missing mid-day intervals, spherical derivatives,
weighted API pacing, batched-response validation, HTML escaping, horizon/model
selection, adjacency/coverage thresholds, stale snapshot refusal, mocked Gmail/
CallMeBot/Twilio acceptance/rejection, uncertain-send suppression, durable SQLite
backup/restore, and Streamlit controls/charts/map-click state.

Compilation and `git diff --check` passed. **Actionlint 1.7.7** accepted the
workflow. Its published release checksum was verified. The embedded state
selection JavaScript also passed seven mocked GitHub scenarios: bootstrap,
previous artifact, current rerun artifact, missing state, later state gap,
expired state, and first-ever rerun without a ledger. The latter refuses to
initialize a replacement ledger.

### Live forecast and dry-run evaluation

The first full batched run encountered 429 errors because multi-coordinate
queries still consume weighted API quota. Pacing was changed to account for
variables, locations and horizon, with a shared 60-second default 429 cooldown.
The 76 missing grid forecasts were then retrieved successfully using that pacing;
successful earlier responses were retained rather than fetched again.

The completed bundle contains **63 site/model forecasts and 432 grid/model
forecasts, with zero outstanding request failures**. Retrieval timestamps span
20:27:22–20:30:52 UTC. Replaying this real bundle through the complete watchdog
returned **exit 0**, rendered both HTML/JSON briefings, and sent no notifications.

| Evaluation | Regions | Wave sites | Convergence coverage | Grid model |
| --- | ---: | ---: | ---: | --- |
| +48 hours | 9 | 12 | 216/216 | ICON-EU |
| +120 hours | 9 | 12 | 216/216 | ECMWF IFS 0.25° |

The evaluated target hours were 20:00 UTC on 25 and 28 October. No daytime
thermal quality is inferred from that nighttime evaluation. Model field gaps
listed earlier remain material; parcel/heat-flux estimates and pressure
interpolation are described in [physics.md](physics.md).

Local evidence is under ignored `var/watchdog-live/complete-bundle.json`,
`var/verified-briefings/`, `var/verified-summary.json` and `var/offline-tests.log`.
These time-specific artifacts are not committed source fixtures.

### Dashboard and map providers

Streamlit AppTest passed without external weather requests. A recorded **real**
bundle was also checked with cloud-base and convergence layers enabled.
Chromium browser checks rendered map tiles, regional metrics, task tables,
pressure soundings and meteograms; a map click changed the inspector coordinate.
A separate live dashboard session fetched current selected-model forecasts,
aviation reports and RainViewer radar. The browser reported no JavaScript
exceptions or failed asset requests in the successful checks.

Leaflet, Leaflet.heat, OpenTopoMap, OpenStreetMap and RainViewer tile endpoints
returned HTTP 200. The live map loaded 40 base/radar tiles. RainViewer metadata
was checked against its expected HTTPS tile host and observed frame paths.
The map legend now uses inline HTML/CSS, removing an unnecessary D3 CDN
dependency that initially prevented the restricted-network map from rendering.

Chromium did not trust the managed environment's proxy certificate directly.
For browser validation, public assets were fetched through Python's trusted,
certificate-verifying HTTPS/proxy client and supplied to the browser. TLS
verification was not disabled. Thus these checks validate the rendered app and
actual upstream responses, but do not establish direct Chromium trust-store
configuration for a newly restored cloud environment.

### Deployment boundary and saved configuration

No live Gmail or WhatsApp messages were sent: notification credentials were
unavailable, and tests used isolated databases/mocked transports. No GitHub
hosted workflow run or push was performed. Live delivery and scheduled-run
activation require default-branch files, enabled Actions and the documented
repository secrets/variables. See [operations.md](operations.md).

The cloud draft save was confirmed for the complete install/start instructions
and network rules, preserving the package-manager preset and previous weather
domains. Added custom domains are `api.rainviewer.com`,
`tilecache.rainviewer.com`, `tile.openstreetmap.org`,
`a.tile.opentopomap.org`, `api.callmebot.com`, `api.twilio.com` and
`smtp.gmail.com`. The final map needs no D3/CDN allowlist addition beyond the
existing `cdn.jsdelivr.net` preset. SMTP still requires TCP 465 on its delivery
host. The draft reports `requires_publish: true`; publication and fresh-task
restoration were not claimed or independently verified.

## BGA task-planning upgrade — 23 October 2026

The expanded offline suite passes **122 tests**, none skipped. Coverage includes
CUP/CSV/JSON/GeoJSON import, source checksums and provenance, complete-list
validation, failed-sync preservation, unchanged-sync behavior and downgrade
rejection. Geometry and flight tests cover ≤35 km corridor spacing, both polar
loadings, wind effects, hourly integration, launch windows, rain/cloud-height
limits, missing midpoint data, local distance classes and daily task storage.
A short clear-weather window cannot be widened by risky launch hours.

Streamlit tests exercise sequence loading, reordering, clearing, date separation,
model/hour changes, tooltips and map-event deduplication. Bot tests verify that
suggestions load only after an explicit action and successful corridor assessment;
missing weather disables loading. Raster tests verify fixed RGB colors, transparent
unsupported areas and preservation of the physical palette through Mercator/PNG
rendering. CUP task order, duplicate-name disambiguation and TSK XML escaping and
observation zones are checked.

Both GitHub Actions workflows pass **actionlint 1.7.7**. Python compilation,
`pip check` and `git diff --check` also pass. No new Python dependency was required.

Live BGA synchronization retrieved **1,390** records from the **2026a** SeeYou
file listed on the Worldwide Soaring Turnpoint Exchange's current BGA index.
A second live check reported **unchanged**, with zero added/removed/modified
records. The saved source hash and attribution identify the exact public download.
The primary Newport-Peace HTTPS endpoint was unavailable with an upstream
certificate error during verification; the validated mirror supplied the list.
TLS verification remained enabled throughout.

A live ECMWF corridor request for **MYN → BIE → MYN**, **282.36 km**, returned all
**6 requested sample forecasts**, with **0 failures**. For **24 October 2026**, all
**18 leg/hour rows** had the required weather and the engine returned **NOT
ACHIEVABLE** with conservative net climbs as low as −1.6 kt. This is an integration
check, not retrospective validation of forecast skill.

Browser validation uses Chromium with the recorded real weather bundle to avoid
repeating the full national weather request. Public map/CDN assets are fetched
through the environment's verified TLS client because Chromium lacks its managed
proxy CA. The default map has no task route; explicit task loading draws the
selected sequence. Browser downloads of CUP and TSK were parsed successfully.
Map-side thermal on/off updates the sidebar, sidebar enable displays the raster,
and disabling removes it. No JavaScript or Streamlit exceptions were observed
in these completed interaction checks; map and marker-cluster assets returned 200.
A direct browser click on the MYN marker selected the published code and exact
52.51865 N, 2.88095 W coordinates. A manual CUP upload was parsed, saved with a
user-supplied label and removed
through Data quality. The live GitHub quarterly schedule has not been triggered
during verification.

Runtime evidence remains under ignored `var/` (test logs, live-task JSON,
browser results, screenshots and downloaded task files). These contain no alert
credentials. No live notifications were sent.

## Floating map modes, accessible help and task-sheet exports · 24 October 2026

Validated with Python 3.12, the pinned dependencies and the bundled DejaVu font.
The full offline suite passes **144 tests**, none skipped, in 15.450 seconds.
`pip check`, Python compilation, `node --check assets/map_modes.js` and
`git diff --check` passed. These checks do not validate an actual flight or
establish certification by Windy, SkySight, BGA or Naviter.

New automated coverage includes six wind levels, log-pressure interpolation,
missing and below-terrain data, the 120-hour timeline across the autumn clock
change, geographic coverage, contour ring geometry, zero/unknown transparency,
ballast-dependent STF, rain-blocked XC/PFD, calibrated radar units and grids,
route completion/undo/clear, 200 km/custom candidates, optimisation objectives,
an optimiser UI flow that verifies and loads a candidate, and clear Help Guide
responses. Per-leg distances/times reconcile to the selected integrated flight.

PDF containers include the map image and page structure. Word documents are
opened with python-docx and their complete tables inspected. CUP waypoint/task
order and explicit observation zones are checked against the TSK XML structure.
Wind-correction direction and WMM2025 validity are exercised. Failed/missing
weather exports retain unavailable flight figures instead of invented values.
Generated QR images were independently decoded with ZXing-C++; the decoded
text exactly matched the intended HTTP file URL. ZXing is a validation-only tool,
not a runtime dependency.

A live ECMWF map request returned **40/40 UK land samples**, zero failures, and
usable wind at all 40 samples in the final hour of the **120-hour** timeline.
The pressure-level bar contains all six requested levels; individually unavailable
levels remain gaps. The live RainViewer manifest and a 512×512 RGBA tile were
retrieved successfully. No calibrated live radar provider was configured.

Chromium checks used a clearly labelled synthetic recorded forecast and an
isolated task store. External assets were fetched through the environment's
verified TLS proxy; certificate verification was not disabled. Checks covered:

- Collapsed menu and switches through wind, thermal, PFD, XC, STF, radar and route.
- Six wind altitude choices, timeline movement, point flag and six-row profile.
- Painted contour canvases and point flags showing XC speed and STF. Leaflet uses
  canvas rendering, so SVG-path DOM counts are not a contour-coverage measure.
- The default radar flag correctly says rate unavailable. Separate synthetic
  calibrated-grid checks distinguish 50.0 mm/h, dry 0.0 mm/h and missing coverage.
- All 18 Help Guide cards at 18 px, 31.5 px line height, left alignment and cream
  backgrounds; the live-context bot answers in numbered steps.
- Physical clicks on MYN and BIE markers, Return home, automatic sheet opening,
  Undo and Clear. Stored codes exactly matched MYN → BIE → MYN before undo.
- PDF, Word, CUP and TSK browser downloads reopened successfully. Direct CUP and
  TSK URLs both returned HTTP 200 and contained the full task declaration.
- No Streamlit exceptions, JavaScript errors or failed external assets in the
  successful dashboard and physical-marker checks.

The PDF was also rendered and visually inspected, and text extracted with
Poppler. Actual instrument hardware/SeeYou application import, a live calibrated
radar feed, and screenshot-perfect matching were not available for verification.
See [operation and data limits](map-modes-and-task-sheets.md).

## Radar point precipitation follow-up · 24 October 2026

The full offline suite passes **151 tests**. New cases cover exact coordinate,
model and hour requests, the preceding-hour accumulation/mean-rate contract,
zero versus null, missing forecast coverage, invalid units/time series/values,
cache separation, service failures and recorded-mode isolation. Radar click
events preserve the clicked coordinates without adding task points or changing
the sounding inspector. JavaScript syntax and dependency checks pass.

A verified live Open-Meteo lookup at 52.518, -2.880 returned **0.1 mm/h** for the
hour ending 09:00 BST. Chromium physical clicks near Long Mynd and Talgarth
then displayed **1.3** and **1.8 mm/h** for the hour ending 13:00 BST. Changing
the forecast slider to 14:00 refreshed the latter point to **3.1 mm/h**, without
another map click. The colour-bar pointers followed those numeric values.
These are dated model outputs, not observed radar measurements or stable test
fixtures. RainViewer tiles remained visible with their independent observed time.

The browser check also opened the source/period details and verified that the
flag's horizontal anchor matches its geographic map point within two pixels.
A ResizeObserver fixes the restored flag's position after late stylesheet/layout
changes. No JavaScript errors, Streamlit exceptions or failed external assets
occurred in the successful check. HTTPS certificate verification remained enabled.

Both workflows and `DeliverySettings.from_env` were checked against the secret
table in `docs/operations.md`: Gmail/CallMeBot use `GMAIL_APP_PASSWORD` and
`CALLMEBOT_API_KEY`; the two Twilio secrets apply only to that optional provider.


## UK wind, airfields, task analysis and wave maps — 24 October 2026

The complete offline suite passes **170 tests**, none skipped. New coverage
includes regular coastal grid geometry, missing 950/800 hPa vector interpolation,
below-terrain and ground-to-10 m fallback, six-level grid coverage, the two-click-
object component contract, full generated JavaScript syntax, named airfields,
map event identity/deduplication, CUP/TSK task import, OpenAir circles/arcs and
corridor crossings, missing DEM samples, candidate path rain rejection, hourly
AGL data and all six wave altitudes. Existing task-sheet/export, radar and alert
checks remain included. Python compilation, four JavaScript syntax checks,
`pip check` and `git diff --check` pass.

### Live weather and airfield checks

A cold ECMWF wind request retrieved all **195 regular-grid nodes**, with zero
request failures, in **105.62 seconds**. All six altitude levels have 195/195
supported values for every one of the **110 available forecast hours** checked,
from 24 October 10:00 BST through 28 October 22:00 GMT. Earlier hours preceding
the returned model data remain unavailable. The current-hour check used four
explicitly labelled surface fallbacks at 950 hPa. A later-hour sweep exposed a
small ground-to-10 m interpolation gap; the regression test and near-terrain
surface fallback now cover it. Separate live UKV checks at Mid Wales, Newtown
and Hereford returned all six levels, with zero failures.

NOAA requests covered **130 unique ICAO codes**, returning **88 METARs** and
**82 TAFs**, with zero request/parser failures. Capability does not guarantee
a current report; some returned TAFs were expired and are marked accordingly.
Browser checks at Long Mynd, Heathrow and Lleweni Parc loaded actual current
Open-Meteo surface estimates and all six trend rows, together with raw NOAA
METAR/full TAF text. Nearby station distances and expired TAF labels were
verified. Missing runway/elevation metadata remains unavailable.

### Browser interaction and timing

Headless Chromium tested the full Streamlit application with a deterministic
synthetic five-day bundle. This is UI evidence, not validation of a real flight.
The map rendered all 1,098 airfield markers; wind flags returned values at Mid
Wales, the Welsh Coast, Shrewsbury, Newtown, Ludlow and Hereford. Wind altitude
button operations took **57–91 ms** in that run, and the pan/zoom JavaScript
operation took **16 ms**. Both generated **zero WebSocket updates** to Streamlit.
These local measurements do not promise instant cold network fetches.

The dedicated planner built **MYN → BIE → MYN** using physical BGA-marker clicks
and Return home. Analysis displayed the verdict, hourly conditions, hazard
assessment and **two candidate energy lines**. All six wave-altitude buttons
switched in **49–109 ms**, also with zero Streamlit WebSocket updates. The
successful combined run had zero JavaScript exceptions, Streamlit exceptions
or failed asset requests. A separate live airfield browser check likewise had
zero script exceptions or failed assets. Public HTTPS assets were fetched via
the environment's certificate-verifying Python proxy client; TLS checks stayed on.

Time-specific evidence is retained locally under ignored `var/`:
`upgrade-browser-results.json`, `wind-grid-live-results.json`,
`wind-grid-all-hours.json`, `ukv-wind-live-results.json`,
`airfield-browser-results.json` and `map-upgrades-final-tests.log`.

### Remaining data boundary

The environment proxy blocked the current UK airspace source sites.
`asselect.uk` and `www.j3j4.org` were added to a confirmed saved network draft,
which requires publication. No current national airspace boundary file was
retrieved/verified or bundled. The tested OpenAir/GeoJSON importer and corridor
intersection engine work with supplied geometry; without it the application
reports airspace as **UNASSESSED**, with major-airfield proximity listed
separately. Wave bars remain Scorer-scale guides, and terrain sampling cannot
establish flight clearance. No live notification was sent.

## Optional WhatsApp, ntfy and verified UK airspace — 24 October 2026

The complete suite passes **179 tests**, none skipped, including all earlier
map, route and export checks. New checks cover whitespace removal from Gmail
app passwords, blank CallMeBot keys, Gmail-enabled exit 0 when optional push
fails, keyless ntfy requests and response validation, and uncertain-send
deduplication. Notification transports are mocked; no live email, WhatsApp or
ntfy test message was sent. Provider acceptance and handset receipt were not
tested with production credentials.

Schema-1 delivery state migrates atomically to schema 2 while preserving existing
claims, payloads, provider IDs, audit events and reference profiles. Previously
sent or uncertain deliveries still suppress duplicates. The workflow retains
the original restored artifact and migrates only its working copy.

Both workflows pass **actionlint 1.7.7**. Every pinned action's exact remote
`action.yml` was checked for the Node 24 runtime: checkout v5, setup-python v6,
github-script v8, upload-artifact v6 and download-artifact v7. Python compilation,
dependency consistency and `git diff --check` also pass. GitHub-hosted execution
after this commit remains a separate deployment check.

The previous network blocker for ASSelect is resolved. Verified HTTPS downloads
produced **801 boundary volumes** for AIRAC 1–28 October and **799 volumes** for
29 October–25 November. The current source SHA-256 hashes match files at publisher
commit `98e023e9b42dfaa9efcfd90d31109fa833d91e32`. ASSelect's upcoming `airspace`
and `rat` arrays match the source YAML at its reported commit
`6b7f9d95535a894ce8bff4972da00fb00eec8ead`. Attribution and provenance are retained
in both GeoJSON files.

All output geometries pass Shapely validity checks. Five crossing rings per
snapshot were split into valid polygons without dropping edges or pieces;
boundary Hausdorff distance is checked at 1e-9 degrees. Tests also exercise
line/circle/arc conversion, source endpoints, vertical limits, exclusion of
gliding concessions, AIRAC selection/expiry warnings, a London CTR crossing and
a distant track with no crossing. These checks verify conversion and source
consistency, not independent accuracy of the AIP transcription or NOTAM activity.

Headless Chromium displayed all **801 current boundary volumes**, enabled their
Leaflet overlay, analysed the recorded MYN–BIE–MYN route, and opened the ntfy
subscription cards in Help Guide. The successful run had zero JavaScript errors,
Streamlit exceptions or failed external assets. This uses synthetic weather to
check the UI; AIRAC selection for 29 October is covered by the automated date
test. The browser check exposed and fixed a Folium layer-menu creation-order
error when adding airspace after building the base map. TLS verification stayed
enabled. Local evidence is `var/airspace-ui-results.json` and
`var/current-airspace-planner.png`.

The new `ntfy.sh` cloud allowlist entry is saved in a draft pending publication.
GitHub-hosted Actions use their own network access. Subscription steps appear in
the Help Guide; only `GMAIL_APP_PASSWORD` is required for the default Gmail plus
ntfy configuration, with no CallMeBot key.

## Canvas planning, terrain wave fields, day stars and OGN · 24 October 2026

The full offline suite passes **192 tests**. Added coverage checks the star
bands and weather caps, missing-hour/precipitation handling, high-priority email
and ntfy headers, OGN schema/units/freshness/privacy/midnight handling, atomic
empty traffic snapshots, deliberate route commits, and display-only airspace
simplification without altering obstacle geometry. Notification transports are
mocked; no live email, WhatsApp or ntfy message was sent.

Terrain tests verify a flat DEM cannot generate wave, an isolated hill produces
both phases, a sustained lee train appears downwind for both westerly and easterly
flow, reversing flow blocks propagation, and unsupported heights stay unknown.
All sixteen bundled elevation grids pass shape/range/hash checks. The download
used 34 verified HTTPS Terrarium tiles, with source and archive hashes retained.
This verifies the data and implementation, not exact crest locations or forecast
accuracy against observed flying.

`scripts/verify_client_maps.py` runs real Chromium pointer interactions against
an already-running recorded dashboard. Use an isolated `DUO_TASK_STORE`, since
it deliberately saves MYN–BIE–MYN. Install Playwright as a development dependency
outside the production requirements, then run:

```bash
python scripts/verify_client_maps.py --url http://127.0.0.1:8504 --proxy-assets
```

`--proxy-assets` is optional and preserves verified TLS using Python's trusted
client where Chromium lacks the managed proxy CA. Screenshots and JSON evidence
are saved under ignored `var/client-map-validation/`.

The final recorded-weather browser pass loaded **1,390 BGA points and 1,098
airfields**, drew the dashed cursor leg, toggled STF/PFD/thermal overlays,
closed at Start, reopened the task sheet, saved again after a rerender and used
Return home. Point clicks, cursor motion and layer changes sent **zero Streamlit
updates**. Each deliberate completion sent exactly one update. The wave check
switched all nine heights, clicked a hill, dragged a cross-section and panned;
these also sent zero updates. Explicit OGN demo playback refreshed locally.
There were no JavaScript errors, Streamlit exceptions or failed external assets.
The pointer check also asserts coordinate agreement: a requested 52.92°N,
3.70°W click resolved within one screen pixel. It caught Leaflet focusing a tall
iframe and scrolling the parent between mouse-down and mouse-up; the bridge now
uses preventScroll while retaining keyboard focus.

Representative measured handler/canvas work on this cloud Chromium instance:

| Interaction | Measured time |
| --- | ---: |
| Task redraw | 2.2 ms |
| Cursor dashed leg | 0.3 ms |
| BGA / airfield canvas draw | 1.8 / 1.2 ms |
| Wave altitude selection, nine bands | 0.7–2.8 ms |
| Clicked / dragged cross-section | 12.7 / 2.9 ms |
| Seven demo traffic icons | 0.4 ms |

These are local handler/draw timings after load, not an end-to-end network or
input-to-photon guarantee on all devices. The initial wave bundle takes roughly
seven seconds to compute in the fixture and about 5–6 MB before transport
compression. Forecast fetches, first asset downloads and explicit save/analysis
still take time. Visual inspection rejected a nominally successful tile provider
that returned API-key-required images; the final keyless Esri greyscale and
shaded-relief tiles were inspected in Chromium.

A separate live-traffic browser check reached the actual same-origin snapshot
backed by OGN's HTTPS UK feed. At 11:42 and 11:47 UTC there were no fresh moving
UK glider reports. The DDB registry identified G-CKML (CN KA); no claim of a live
G-CKML sighting is made. A clearly identified **TEST FIXTURE**, intercepted only
inside the browser test, verified heading, 5,000 ft / 60 kt / +3.2 kt popup units,
local click handling and removal after 120 seconds with auto-refresh paused.
No test aircraft was written to the live snapshot or upstream OGN service.

All JavaScript assets parse with Node, Python compilation and dependency checks
pass, both unchanged workflows pass actionlint 1.7.7, and `git diff --check` is
clean. Required HTTPS hosts were saved in the environment draft; actual OGN,
registry, elevation and Esri access succeeded with certificate verification.

## Watchdog state recovery and on-demand Gmail test · 24 October 2026

The full offline suite passes **202 tests**, including ten new recovery/manual
email tests. The GitHub script regression reproduces Run #1 uploading before
failure, Run #2 having no artifact, and Run #3 successfully discovering Run #1.
It also checks current-run attempts, expired artifacts, foreign workflow/branch
artifacts, API failure and explicit recovery selection. The real workflow shell
routes manual `send=true` to `--test-email`, scheduled sends to `--send`, and
manual `send=false` to `--dry-run`.

Recovery tests exercise absent, empty, JSON `{}`, corrupt, future-schema and
wrong-column SQLite artifacts. These cases initialize an empty verified ledger;
valid artifacts retain accepted delivery claims, schema-1 files migrate, and
invalid local working copies are retained separately. Normal scheduled
idempotency and uncertain-send tests continue to pass with valid state. The new
recovery policy deliberately permits repeat alerts after state loss; it
supersedes the earlier first-run-only initialization behavior described above.

Two consecutive explicit manual tests each submit one Gmail message despite
having no weather trigger and without consulting the scheduled ledger. Actual
MIME composition is checked with mocked SMTP: fixed requested recipient,
whitespace-stripped app password, unique test Message-IDs, and five dated
one-star rows in a poor-weather fixture. Forecast outage/stale data still sends
the delivery test with unavailable ratings. A genuine SMTP rejection remains a
reported failed delivery. No email, ntfy or WhatsApp test was sent live.

Both workflows pass actionlint 1.7.7; the GitHub discovery JavaScript parses with
Node, Python compilation passes and `git diff --check` is clean. The GitHub API
was inaccessible from this execution environment (HTTP 403), so the reported
Actions failure was reproduced from the supplied error and checked-in workflow,
not by reading private run logs or dispatching a production job.

## Unified map mounting and pilot briefing · 24 October 2026

Maps no longer depend on Streamlit's `tab.open` lazy-rendering state. A small
supported two-way Streamlit component renders all four map containers with a
720 px iframe and explicit 680 px Leaflet/CSS height. ResizeObserver,
IntersectionObserver and a visibility timer restore hidden canvases. Leaflet and
jQuery are bundled with licences; unpkg/cdnjs provide fallbacks. Explicit
Finish/Analyse events retain task saving; mouse movement never calls Python.
Nested NumPy arrays/scalars, NaN and infinities are normalized to JSON null before
custom browser payload serialization. This removes map-mount failure paths
without interpreting missing weather as zero.

The committed `scripts/verify_client_maps.py` was run against the full recorded
app in Chromium, with an isolated task store and verified HTTPS asset proxy.
It asserted both Task and Wave maps are visible at ≥600 px, all 1,390 BGA points
and 1,098 airfields are present, rubber-band/closing/Undo/Return Home work, all
three soaring overlays toggle, all nine wave heights and click/drag sections
work, and OGN demo remains explicitly labelled. Task redraw measured 2.0 ms,
cursor update 0.3 ms; wave switches 0.6–2.9 ms after preload. Cold forecast and
terrain preparation are separate and these timings are not device guarantees.
Local gestures sent zero Streamlit updates; deliberate finishing sent one.

Both exact requested bot questions passed in the browser. The route became
`MYN → MNDN → MNDS → MYN`, with per-leg weather/STF and a complete Mynd hourly
briefing. Neither answer contained `THE — Thetford` or a request to build a task
first. With the selected model deliberately absent from the snapshot, Task,
Wave and OGN maps and their canvases still measured **680 px**. There were no
browser JavaScript errors or failed assets. Runtime screenshots and results are
under ignored `var/unified-final-browser/`.

A real certificate-verified ECMWF Long Mynd request returned **169 hourly rows**,
from 23 October 13:00 UTC to 30 October 13:00 UTC. Today's 24 October rating was
**one star**, with zero estimated thermal XC distance, rather than unavailable.
The response is retained locally in `var/unified-live-mynd.json`. Both single
and batched requests now include `past_hours=24`; parser and quota accounting
include those hours. This is model history, not past observations.

Gemini SDK configuration, read-only route tool, credential precedence and
provider-failure fallback were tested with mocks; no Gemini key was available
for a live generation. The local assistant was tested in the real browser.
No email, WhatsApp or ntfy notification was sent. Existing watchdog discovery,
invalid-state recovery and manual email tests remain part of the full suite;
there is no `Previous state unavailable` throw in the workflow or alert runner.

Final full suite: **211 tests passed** (`python -m unittest discover -s tests -v`,
119 seconds). `pip check`, Python compilation, generated map JavaScript checks
and `git diff --check` passed. The final browser process exited successfully.

## Free-form REST assistant, elastic tasks and continuous wave field

Validated on **24 October 2026**, Python 3.12 and headless Chromium.

- Full suite: **220 tests passed** in 132 seconds. Coverage includes account model
  discovery/pagination, API-key and bearer headers, credential-redacted HTTP
  errors, disabled-model fallback, quota handling, ordinary English versus BGA
  codes, site-specific ridge headings, five-day hourly evidence, grounded route
  proposals, missing weather and continuous terrain/wave blending.
- Dependency consistency, Python compilation, JavaScript syntax and
  `git diff --check` passed.
- Browser local fallback answered the described Mynd ridge route and a weather
  forecast with hourly winds, sector thresholds, five-day stars and both national
  circuit assessments. No 250 km gate or lowercase-code substitution appeared.
- Browser REST integration used an **explicit mock provider**: account discovery,
  a 404 for `gemini-3.1-pro-preview`, fallback to `gemini-3-flash-preview`, free-form
  response, checked route proposal and loading `MYN → MNDN → MYN` all passed.
  Clicking Denbigh on the map, leaving for the bot and asking for the weather
  forecast “here” also selected Denbigh’s site briefing correctly.
  A separate verified-HTTPS request reached Google's actual models endpoint and
  returned an authentication error for an intentionally invalid credential.
  No valid account key was available; live generation/model entitlement is
  therefore **not verified**. The model list makes no unsupported availability
  claim about Gemini 3.8 or any other version.
- Physical mouse checks snapped a cursor 15 px from CVN exactly onto the point,
  replaced a turnpoint, inserted one through a midpoint handle, and moved linked
  Start/Finish points. There were **zero server updates during movement and one
  per drop**. Measured elastic updates were 1.1–2.9 ms on this test machine.
- Wave browser checks found one national domain with BGA dots hidden. Terrain
  and supported wave values were sampled over Snowdonia, Cambrian Mountains,
  Brecon, Mynd, Pennines, Lakes, Cheviots, Highlands, Cairngorms and NW Highlands.
  All nine height switches stayed in the browser (0.7–2.3 ms measured). Clicked
  and dragged cross-sections also passed, with zero server updates.
- General map checks reported zero JavaScript exceptions or failed public assets.
  Task, Wave and Traffic maps remained visible with missing weather. Synthetic
  recorded weather tests exercise rendering and logic, not meteorological skill.

Public map assets were obtained through Python's certificate-verifying HTTPS
client for Chromium in the managed proxy environment. No TLS verification was
disabled. Wave elevations were fetched from the attributed real Terrarium data;
the resulting ~2 km full-domain linear model remains an independent diagnostic,
not a reproduction or validation of SkySight forecasts.

Reproduction scripts: `scripts/verify_client_maps.py`,
`scripts/verify_route_drag.py` (recorded app on port 8504), and
`scripts/verify_gemini_browser.py` (isolated `scripts/gemini_browser_fixture.py`
on port 8505). Both apps require a recorded `DUO_DASHBOARD_SNAPSHOT` and should
use separate `DUO_TASK_STORE` directories. The Gemini fixture patches only the
transport for its process; it is never imported by the production app.


## Lazy pages, shared tasks and local briefings · 24 October 2026

The full automated suite passed: **234 tests in 25.8 seconds**. Dependency checks,
Python compilation, JavaScript syntax checks and `git diff --check` also passed.

The page router now builds only the selected page. AppTest checks reject hidden
wave calculations, OGN worker starts and map rendering on all three text pages.
The expanded tests cover cached read-only terrain preparation and numerical
solver equivalence, forecast-hour cache identity, coordinate precision, compressed
map round-trips, credential-separated model discovery, versioned task clearing,
local scope, UK-only geocoding and ambiguous/partial place names.

Chromium verification used the recorded five-day weather fixture:

- `verify_lazy_pages.py`: one iframe on the selected map page, zero on text
  pages; task adds and clearing from either toolbar synchronize both maps.
  Point saves preserved the browser map object and sent about 1–2 KB of state.
- `verify_route_drag.py`: physical magnetic snap error 0 m in the fixture;
  middle-point replacement, midpoint insertion and linked Start/Finish moves
  passed. Drag handlers took 0.8–1.0 ms, with no movement packets and one per drop.
- `verify_client_maps.py --proxy-assets`: all 1,390 turnpoints and 1,098 airfields,
  task overlays, cursor line, completion, nine wave heights, click/drag sections,
  UK wave coverage, OGN demo and missing-weather maps passed. No JavaScript errors
  or failed public assets. Cursor update 0.7 ms; wave switches 0.5–0.9 ms after preload.
- `verify_gemini_browser.py`: explicitly mocked model discovery and 404 fallback
  from Pro to Flash, strictly local Denbigh answer, an explicitly requested route
  proposal, route loading and a subsequent “here” briefing passed. This does not
  verify a live Google credential or generation quota.

In a separate AppTest timing run, text-page Python work took 5.6–20.7 ms. Warm
Wave Watch took 257 ms, versus 5.25 seconds for its first field/document build.
The browser's warm page-ready checks measured about 211–553 ms for text pages,
867–976 ms for Task/Weather and 1.03–1.04 seconds for Wave Watch. These are measured
fixture timings, not a guarantee of subsecond end-to-end response on every
browser. Cold provider requests and Gemini generation still depend on the network.

The representative Task Map document including airspace fell from 1,478,226 to
285,647 bytes (80.7%) with compact display geometry and deflate/base64 transport.
The source catalog and export coordinates retain original precision; the obstacle
checker still uses unsimplified geometry.

Live verified HTTPS lookup resolved Birmingham to 52.48142, −1.89983 and Church
Stretton to 52.53778, −2.80149 through Open-Meteo/GeoNames. The latter regression
also ensures the shorter BGA name “Stretton” cannot replace “Church Stretton”.
No email, ntfy or WhatsApp notifications were sent during this validation.

The uploaded request ended mid-sentence after “cloud base, and spread”. All
complete requirements were implemented; the local lift breakdown includes
spread-out risk from the forecast evidence.

## Streamlit Cloud stale-import hotfix · 24 October 2026

**240 tests passed in 25.5 seconds**. Python compilation, dependency checks and
`git diff --check` also passed.

The reported startup failure was consistent with a pre-deployment `ogn_traffic`
module retained in `sys.modules`: the committed source already defined
`set_worker_active`, but a direct symbol import could still fail. All OGN entry
points in `app.py` now use optional module loading and guarded attribute lookup.

Regression coverage includes absent OGN hooks, a failed OGN import, worker lease
behaviour without starting another thread, old function signatures, real reloads
of ten cached application modules, and an audit of every local symbol imported
by `app.py`, `briefing_bot.py`, `client_map.py` and `ogn_traffic.py`.

Headless Chromium passed normal startup and shared-task/page navigation using
recorded weather. A second isolated server deliberately removed the new APIs
from ten loaded modules and supplied an OGN module with no hooks. Weather, Task,
Wave, traffic fallback and Help pages opened without a Python exception or
JavaScript error. Current modules were not reloaded again after recovery, and
OGN's worker object was preserved. These are local deployment simulations;
Streamlit Cloud's hosted process was not accessed directly.


## UK grid / single-page rendering fix · 24 October 2026

**247 tests passed in 34.1 seconds.** This includes the missing-grid fallback,
hot-reload API checks and refreshing the compatibility registry itself in an
already-running process. Dependency, compilation and whitespace checks passed.
No notifications were sent.

The old spinner was in `app._main`, around `load_map_forecasts` →
`planning_ui.fetch_map_grid` → `OpenMeteoClient.fetch_batched`. It already excluded
day/hour from its cache key and sat inside the Weather guard. Its transport
nevertheless applied weighted request pacing, 20-second timeouts and up to three
attempts. The spinner also surrounded cache hits. Shared summaries were rendered
before the final page branches, and Streamlit could retain an old page body while
waiting for synchronous work. No `st.tabs` wrapper remained in the starting code.

The replacement has a cycle-only shared resource, a session reference to it,
lazy model-separated Futures, twelve concurrent chunks, 120 forecast hours,
3.5-second request timeouts and zero retry sleeps. Failed chunks stay unavailable
for that cycle. All seven pages now return through exclusive branches; the body
is cleared before rendering. The Task optimiser cannot initiate a national grid.

`tests/test_grid_navigation.py` verifies all of the following:

- A deliberately blocked provider starts twelve concurrent requests and returns
  control in under 300 ms. Repeated calls reuse its Future; failures do not retry.
- Rate limiting and timeout make one request each and never sleep.
- Both cached resources and all 120 exact hour views retain identity across
  day/hour changes; NumPy view selection is under 5 ms without copying data.
- A pending grid cannot block Help. All six non-Weather pages avoid the loader,
  including Task optimisation. Models and cycles remain separate.
- Real, warmed Streamlit pages (no mocked maps/physics) switch in under 300 ms;
  no national-grid spinner is displayed and no duplicate page headings/cards
  remain. Full-suite measured wall-clock times were:

| Page | Warm AppTest execution |
| --- | ---: |
| Weather map | 83.7 ms |
| Task planner | 61.0 ms |
| Help Guide | 50.6 ms |
| Wave watch | 78.7 ms |
| Live OGN Glider Radar | 52.4 ms |
| Help & Briefing Bot | 51.3 ms |
| Data quality | 61.5 ms |

Chromium `scripts/verify_lazy_pages.py` checks all seven pages, exclusive Task/Wave
headings, exactly one iframe on map pages and zero on text pages, task additions
and clearing across maps, fragment-only saves, local briefing and altitude
switching. No JavaScript errors occurred. Latest warm browser readiness ranged
from 256 ms for Help to 873 ms for Wave; these include iframe loading and browser
layout, and are **not** the under-300-ms server measurement. A first uncached Wave
build in the earlier fresh-server run took 5.58 seconds. Cold Task/Wave physics and
new map document construction can still take several seconds.

A live verified HTTPS sample at Long Mynd returned 144 timestamps (120 future
plus 24 past) in 1.027 seconds using the new transport. This checks the API schema
and transport, not national throughput. The full UK grid's completion time still
depends on provider transfer/rate limits and worker processing; no unconditional
under-two-second cold-network guarantee is claimed. Its wait no longer blocks the
Streamlit page. Tests use deterministic transport, never a live quota dependency.

## Opt-in point forecast and warm navigation · 24 October 2026

**257 unit tests passed in 50.870 seconds.** Compilation, dependency and JavaScript
syntax checks passed. The probe is optional and confined to Weather. Tests cover
two local dates across both daylight-saving changes, 07:30–18:30 sampling, cached
preparation, consistent thermal physics, upper pressure levels, missing data,
old cached profile/grid compatibility and local recovery from an import failure.
The deliberately failed optional import logs a traceback without crashing any page.
Transport-notice tests validate hashes and keep cache messages out of task state.

Warm full-suite AppTest execution measured 65.9–99.7 ms across all seven pages.
The existing grid tests still assert <300 ms, <5 ms in-memory hour views, no
national-grid spinner and no grid restarts on day/hour/page changes. No forecast
transport or map constructor is mocked in the page-render timing portion.

The headless Chromium probe script uses actual mouse clicks and a varying,
explicitly synthetic recorded weather bundle. It checks normal airfield popups
when off; coordinate, airfield and BGA inspection when on; 24 plotted columns;
LCL arithmetic, wind pennants and the nonlinear altitude scale; missing coverage;
both close mechanisms; and automatic reset on navigation. Each point click must
draw and reach the next paint frame in <150 ms and produce **zero Streamlit
events and zero external requests**. The final coordinate/airfield draws took
58.2–77.8 ms and reached the next paint frame in 65.9–93.3 ms. Overlapping BGA
and airfield markers select the closest pixel hit, preserving the correct
feature coordinates and elevation.
It checks all seven pages, with one map only where appropriate and no probe
elements elsewhere. Browser cache eviction must recover the map through the
component protocol. Full chart and panel screenshots are retained under the
ignored `var/point-probe-browser/` directory for visual review.

The warm browser timing assertion measures the actual navigation click to map
construction completion (or text heading insertion), excluding Playwright's
actionability wait/polling. It retains three samples per page and asserts a
median below 500 ms; individual samples can exceed this during scheduling
jitter (latest samples reached 568 ms for Task and 505 ms for Wave). Latest
per-page medians were 210–477 ms. This is distinct from cold construction:
the final cold Wave check took 6.68 seconds, including its settling wait. Network/tile completion and performance on Streamlit Cloud
are not guaranteed by these local timings. Browser caches contain only decoded
documents, never inactive map instances or listeners.

`verify_lazy_pages.py` also passed task additions/clearing across Weather and
Task, fragment-only saves, single-page headings, local briefing and Wave controls.
`verify_route_drag.py` passed physical snap/replace/insert/linked-endpoint edits;
drag updates measured 1.0–1.5 ms, with no events during drag and one on release.
Both reported zero JavaScript errors. No notifications were sent.

A verified live Open-Meteo point request with the expanded 96-field schema
returned 144 timestamps in 2.109 seconds, with usable 250/200 hPa heights near
34,246/39,004 ft. Its 985.7 hPa surface pressure prompted explicit off-scale
markers on the fixed 990–1030 hPa plot; numeric values remain unclipped. This
checks schema compatibility, not national-grid throughput. API model-init time
is unavailable, so the chart labels retrieval time honestly. Point values are
cached model-grid estimates, not coordinate measurements or measured clouds.


## Point forecast visual redesign — 24 October 2026

The Weather-only probe now has a white hourly grid, a sticky 165 px legend,
local sunrise/sunset, explicit altitude ticks, nested thermal contours and
scalloped cloud columns. The solid black curve is the mixing top; the bordered
usable-height rows exclude cloud depth and retain terrain MSL / zero AGL during
unsoarable hours. Wind remains labelled 10 m because the source has no 2 m wind.
Observations retain their station, coordinates, timestamp and stale status;
missing reports never become synthetic observations or a blended forecast.

The recorded-app Chromium probe regression passed coordinate, airfield and BGA
clicks, close/disable/re-enable, all seven pages, cache-miss recovery and default
OFF isolation. Actual coordinate/airfield DOM + canvas draw times were 24.1–58.4
ms, reaching the next animation frame in 25.4–65.7 ms, with no click-time network
requests or Streamlit events. Synthetic shallow, towering, overcast, blue,
missing-data and 2× device-pixel-ratio scenes all passed the 100 ms drawing limit.
The test checks cloud depth/stack count, real observed points, approximate solar
times, unsoarable terrain baseline, 216 bordered data cells and the legend's fixed
position during horizontal scrolling. Full diagrams are retained locally under
`var/point-probe-browser/` for visual review.

Warm page-switch medians were 199–418 ms across three samples per page. These are
local Chromium timings with recorded forecasts, not a guarantee for every device
or Streamlit Cloud deployment. The browser harness now waits for sidebar updates
before pressing Escape, which is Streamlit's stop-running keyboard shortcut.
A separate production component fix prevents late map acknowledgements from
cancelling asynchronous map replacements; its controlled Node regression fails
against the previous implementation and passes with the fix.

The full automated suite passed **260 tests** in 50.45 seconds. Dependency checks,
Python compilation, JavaScript syntax and whitespace checks passed. The lazy-page
browser test passed single-page rendering and shared task additions/clearing;
route dragging passed snapping, replacement, insertion and linked endpoints with
1.2–1.3 ms drag updates and no JavaScript errors. The separate Gemini fixture
passed model fallback, tomorrow's route loading and a clicked Denbigh briefing;
it uses an explicit mock provider and no real Gemini request. Its harness waits
for the requested date to reach the sidebar before switching pages.

The broader client-map browser regression also passed: one user-edit event per
route action, STF/PFD/thermal overlays, nine wave altitude controls, click/drag
cross-sections, all ten terrain coverage checks, seven demo gliders and maps with
missing model data. Its event accounting separates cache acknowledgements and
explicit task recovery messages from actual user edits. All five headless
browser scripts passed; no JavaScript errors or notifications were produced.


## Duo thermal-core correction and cloud layers — 24 October 2026

The full automated suite passed **266 tests** in 36.73 seconds. New cases cover
300–350 W/m² total radiation and 500–600 m AGL depth, a single polar-derived sink
deduction, zero night-time climb, positive usable height, ballast sensitivity,
pressure cloud fields, separate UKMO/ECMWF API columns and legacy UK snapshots.
The rain-gap integration test now uses a fixed cruise speed so its launch-window
assertion tests temporal gaps independently of thermal recalibration. A final
focused run passed 19 tests after extending legacy UK model selection.

`verify_thermal_probe.py` executed the real Python and JavaScript implementations
with labelled synthetic **Sun 25 Oct** cases. The dry case produced 1.8 kt during
325 W/m² heating and 1,600 ft usable AGL at the chosen 1,460 ft site elevation.
Night cells stayed at 0 kt / 0 ft AGL / terrain MSL. Dry, ballasted and paired-model
renders took 16.6–64.8 ms; pixel checks confirmed blue contours, grey cloud decks
and white cloud puffs, with shading in low/mid/high bands. The two-day paired test
used UKMO on day one and distinctly labelled ECMWF on day two, without mixing
variables across those source profiles. Actual charts are retained under the
ignored `var/thermal-probe-browser/` directory.

The full Streamlit probe browser passed coordinate/airfield/BGA clicks, default
OFF, close/re-enable, isolation across all seven pages, cache recovery and high-DPI
scenes, with no click-time requests/reruns or JavaScript errors. Coordinate and
airfield draws reached the next paint frame in 25.7–42 ms; warm navigation medians
were 189–323 ms. These are local recorded-fixture measurements, not a guarantee
for every client or deployment. The separate lazy-page, physical route-drag and
mocked Gemini browser checks passed as well; drag updates were 0.8–1.1 ms.

A real, verified-TLS request to Open-Meteo used
`models=ukmo_seamless,ecmwf_ifs025`, `cell_selection=land` and the full 108-field
schema at Midland Gliding Club. Both models returned successfully within the
configured single-attempt 3.5-second request timeout. Both omitted BLH, so the
labelled dry-parcel fallback supplied mixing depth. On 25 October at 11:00–14:00
UTC, ECMWF yielded 1.1–1.5 kt and roughly 1,120–1,524 ft usable AGL; UKMO differed
because its temperature, radiation and pressure profiles differed. No values
were forced to match SkySight, and this check does not validate model skill.
No alert emails, WhatsApp messages or push notifications were sent.

The broader client-map browser also passed route editing and completion, all
three task soaring overlays, nine wave altitude controls, click/drag sections,
all terrain coverage checks, demo traffic and missing-model map rendering. All
six browser scripts passed. Dependency, Python compilation, JavaScript syntax
and whitespace checks were clean.

## Model coverage repair — 24 October 2026

The complete automated suite passed **274 tests**. New regression cases remove
BLH from every provider, remove UKMO CAPE, insert cloud gaps, and end UKMO/ICON
upper fields after 48 hours. All three selected models retain 8/8 covered local
hours and valid ratings on five days, including the BST/GMT transition. Tests
also cover native zero preservation, exact UTC donor alignment, primary outage,
all-provider missing radiation, incompatible requested coordinates, snapshot
provenance, changed-donor cache invalidation, partial regional-batch failures,
and raw transport reuse across model switches. Warm page execution measured
under 0.3 seconds on all seven pages; hour changes did not restart grid loading.

`scripts/forecast_coverage_fixture.py` supplies explicitly synthetic recorded
weather for `scripts/verify_forecast_coverage.py`. Chromium exercised all **15
model/day combinations** using the real dropdowns and physical map clicks.
Every combination showed five Star Ratings, 8/8 coverage on every card, a visible
map, and positive midday net climb and usable AGL in the actual JS probe. Probe
draws took at most **38.4 ms**, reaching paint within **43.3 ms**. There were no
JavaScript errors, click-time network requests, or probe-triggered Streamlit
reruns. The separate dry/ballasted/paired thermal-render regression also passed.
Screenshots and timing records are retained locally in `var/coverage-browser/`.

Two verified-HTTPS live checks exercised the real provider boundary. A single
Long Mynd request across three independent models completed in **1.84 seconds**.
Every selected model then produced 8/8 covered hours and valid five-day ratings.
At 12:00 UTC on 25 October, UKMO/ICON/ECMWF gave respectively **1.6/1.8/1.5 kt**
net climb and approximately **2,920/2,120/1,482 ft** usable AGL. BLH was estimated
from bracketed parcel soundings, rather than assigned a positive constant.

A subsequent 23-site live regional check took **5.33 seconds** and encountered
real transport failures: all UKMO batches and one batch from each other provider
timed out. Surviving ECMWF/ICON responses jointly covered all **23 sites**, so
each dropdown choice still produced five valid Long Mynd ratings with 8/8 local
coverage. The borrowed model fields remained labelled. These are measured API
responses, not a promise of provider uptime or validation of forecast skill.
No alert email, WhatsApp message or mobile push was sent.

## Vertical moisture and cloud graphics overhaul

The full suite passed **280 tests**. The new moisture tests cover Magnus RH and
dewpoint recovery, ice supersaturation, true zero preservation, hourly donor
provenance, 900 hPa interpolation and all three models' request schemas. A Node
test executes the production JavaScript classifier against nine synthetic
profiles and the exact Cu depth, 150-ft LCL proximity, RH, CAPE, lifted-index,
inversion, precipitation and ice-temperature boundaries.

`python scripts/verify_cloud_graphics.py` passed nine Chromium image checks:
humilis, mediocris, congestus, spread-out, Cb potential, stratus, cirrus, clear
and missing-data scenes. Canvas draws took **6.1–9.0 ms** locally. Pixel checks
distinguished shaded towers, silver stratus and translucent upper ice wisps;
cloud bases stayed on the LCL. The pressure-profile selector and explicit
34 kg/m² loading label also passed. Images and measurements are retained under
the ignored `var/cloud-browser/` directory. These are schematic forecast-cloud
illustrations, not rendered observations or validation against SkySight data.

The full point-probe browser passed off/on, coordinate, airfield and BGA clicks,
close/re-enable, seven-page isolation, high-DPI rendering, vertical scale,
cached observations and document-cache recovery. Coordinate and airfield draws
took **47.0 / 25.7 ms**, reaching paint in **52.6 / 27.4 ms**, without click-time
network requests or Streamlit reruns. The existing dry/ballasted/paired thermal
regression passed unchanged core/sink and usable-height checks. A separate real
dropdown/browser pass covered all **15 model/day combinations**, with five valid
ratings, 8/8 coverage, working maps and probes; maximum draw/paint was
**37.8 / 43.2 ms**. No JavaScript errors were reported.

Live verified-HTTPS requests confirmed the expanded **129-field** contract for
ECMWF, ICON and UKMO. UKMO returned 900 hPa temperature, dewpoint, RH, cloud cover
and CAPE; its missing showers can use a labelled donor. Lifted index remained
unavailable when every provider omitted it. The first full-horizon UKMO request
timed out, while ECMWF/ICON supplied usable fallback profiles; a subsequent
two-hour UKMO schema check succeeded. Pressure levels below model terrain were
correctly excluded. Provider omissions were not converted into invented clear
sky or instability. No notifications were sent during validation.

Dependency, Python compilation, JavaScript syntax and whitespace checks passed.

## Bounded watchdog recovery — 29 October 2026

The reported #20 run was on `4702d35`. GitHub's Actions API returned Forbidden
from this environment, so its private step logs could not be independently
retrieved. Inspection of that exact checkout found `failed = bool(bundle.errors)`
and an additional no-regional-data failure, both returning exit 2. The watchdog
also used the general three-attempt, 20-second HTTP client with a shared weighted
pacer, then fetched convergence separately. Its workflow installed all app
dependencies and ran the complete test suite on every schedule. These paths
explain how provider gaps could both delay and fail the job. State recovery was
already nonfatal; the quoted empty-ledger warning was not an exception.

The watchdog now shares a 12-second weather deadline across at most twelve
single-attempt workers, preserves successful batches and applies the same hourly
UKMO/ICON/ECMWF completion as the dashboard. Scheduled operational warnings stay
green and appear in reports; programming exceptions still fail. The artifact
name is consistently `soaring-watchdog-state`, retained for 30 days. Discovery
checks candidate workflow provenance under a six-second deadline. Full tests
are retained in a separate push/PR/manual workflow.

`python alerts.py --dry-run` against live verified-HTTPS Open-Meteo completed in
**15.472 seconds wall clock, exit 0**. This included real timeouts: hourly donor
support recovered all **23 regional sites for all three selections**; 166 of
216 convergence points were available per model. Incomplete convergence stayed
explicit. At Long Mynd, each model produced **8/8 covered local hours** and valid
Star Ratings on **29 October–2 November**. No ratings were manufactured for
missing weather. Runtime logs/snapshots are ignored under
`var/watchdog-live-check/` and `var/watchdog-live-timing.json`.

A fresh Python 3.12 environment installed only `requirements-watchdog.txt` in
**7.70 seconds**, passed `pip check`, and replayed that complete live snapshot in
**6.205 seconds, exit 0**. GitHub runner startup and artifact service latency
were not measured; these local results do not guarantee an Actions job duration.

The complete unit suite passed **291 tests** in **62.918 seconds**. New tests
exercise HTTP 429 without sleeps, stalled DNS/body deadlines, partial-result
preservation, the twelve-worker ceiling, actual subprocess exit with blocked
daemon workers, all three models' five-day repair, total-outage exit 0, timed-out
notification claims, deferred channels, code-crash propagation, fresh-state
recovery, workflow routing and 30-day artifact retention. Notification tests
use mocked providers; no email, WhatsApp or ntfy notification was sent.

All **eight** browser verification scripts passed: cloud graphics, thermal
probe, model coverage, full point probe, lazy pages, route drag, mocked Gemini,
and the broader client maps. The nine cloud-family scenes drew in at most
**11.7 ms**; all **15 model/day** cases retained five ratings, 8/8 coverage,
working maps and positive synthetic midday probe lift/height, with maximum
draw/paint **51.0/56.9 ms**. These retain the existing scalloped Cu/towers,
spread-out rolls, stratus bands, ice wisps and potential storm anvils, plus the
explicit wing-loading label. No extra moisture is invented during an outage.
Browser artifacts remain under ignored `var/` directories. Compilation,
JavaScript syntax and `git diff --check` also passed.
