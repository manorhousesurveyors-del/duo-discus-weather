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
