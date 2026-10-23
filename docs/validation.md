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
