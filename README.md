# Duo · UK soaring weather

A Streamlit forecast dashboard and six-hour alert watchdog for the original
Schempp-Hirth Duo Discus G-CKML. All five modules are implemented: configuration
and storage, ingestion, soaring physics, alerts, and the interactive dashboard.

## Install and run

Use Python **3.12** with IANA timezone data. Run from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m streamlit run app.py
```

The dashboard provides nine regional forecasts, fourteen wave sites, thermal and
cloud-base maps, convergence ribbons, custom BGA tasks, observed radar, METAR/TAF
popups, and a map-click sounding/meteogram inspector. Choose model, day, local
hour, wing loading and layers in the sidebar. Times follow **Europe/London**,
including winter GMT. The initial view selects tomorrow at 13:00 after today's
13:00 has passed. Refresh weather explicitly to request new data; cached
forecasts last 15 minutes and aviation observations 60 seconds.

Public weather APIs need no credentials. The first map/grid load can take a few
minutes because requests respect Open-Meteo's weighted quota. Allow about 2 GB
RAM for the dashboard and land mask; the mask alone expands to about 1 GB.
Dependencies are pinned in [requirements.txt](requirements.txt).

## BGA tasks, thermal map and briefing assistant

The bundled BGA **2026a** catalog contains **1,390** published turnpoints with
trigraphs, coordinates and elevations, attributed to the Worldwide Soaring
Turnpoint Exchange's unofficial mirror. The map starts without a preset route.
Build daily tasks by searching codes/names, clicking BGA markers or coordinates,
or explicitly loading a preset; reorder, clear and export CUP/XCSoar TSK files.

Every custom leg is checked hourly using sampled corridor forecasts, the chosen
34/43 kg/m² Duo Discus polar, conservative climbs, cloud height and wind. The
planner reports achievability, launch windows, elapsed time and the weakest leg.
A separate local day-capacity estimate highlights 100/250/300/500 km potential.

The collapsed floating **Map modes** menu selects wind animation, observed radar,
thermal contours, potential flight distance, XC speed, speed to fly or the route
planner. The default map is clean. Soaring contours use stepped purple-to-red
bands with black outlines; zero/unsupported areas stay clear. Bookmark `?thermals=on` to start with
it enabled. **Help & Briefing Bot** answers from the selected forecasts/catalog
and checks suggested BGA triangles and out-and-returns before offering to load
them. It requires no LLM key. Controls, metrics, table columns and map markers
have hover explanations.

**Help Guide** provides dyslexia-friendly step cards and a plain-language weather
bot. Completing a route opens its **BGA / SeeYou Task Sheet**, with PDF, Word,
CUP/TSK downloads and direct HTTP links/QR codes. The route optimiser checks
200/300/500 km or custom-distance BGA candidates before loading a complete flight.
See [map modes, radar limits and task-sheet operation](docs/map-modes-and-task-sheets.md).
Radar clicks show Open-Meteo's hourly mean precipitation in **mm/h** for the
selected coordinate, model and forecast hour, with a marker on the colour bar.
These estimates are separate from RainViewer's observed image tiles (native
zoom 7); calibrated observed rates and higher-resolution radar require a
separate provider feed. These are independent
Windy/SkySight-inspired displays, not those services' proprietary products.

Data quality supports CUP/CSV/JSON uploads and a manual BGA update check.
[Quarterly synchronization](.github/workflows/update_bga_turnpoints.yml) runs
at 06:00 UTC on 1 January, April, July and October and commits validated catalog
changes to main. See [task planning and data provenance](docs/task-planning.md)
for source attribution, supported formats, assessment limits and operating details.

## UK wind, airfields and route analysis

Wind uses a complete **195-node rectangular grid** from 49.8–59°N and
8.5°W–2°E, including coastal water. Bilinear canvas interpolation removes the
old distance-cutoff holes. All six wind altitude frames are cached together;
wind/wave altitude changes, panning and zooming stay in the browser.
Initial live downloads still depend on provider latency and quotas.

The **1,098-entry UK airfield layer** includes the named gliding clubs and
130 distinct NOAA reporting stations. Hover for current model wind and surface
weather; click for runway information, latest METAR, full TAF and a six-hour
trend. Missing or old reports are labelled. Airfield status and landing
permission require an operator check.

**Task planner** has its own BGA/airfield map, CUP/TSK task import and an
**Analyse Task & Route Weather** button. Analysis adds hourly leg conditions,
STF/XC speeds, sampled terrain margins, coastal/rain warnings and candidate
energy lines. Bundled ASSelect airspace boundaries are selected by the forecast date, with
AIRAC validity shown. Current OpenAir/GeoJSON uploads can override them.

**Task planner** draws browser-local routes with a cursor-following dashed leg,
distance/bearing labels and STF, thermal and PFD overlays. Finish or Analyse
saves the draft; individual clicks do not reload the forecast. BGA points and
airfields share viewport-culled canvas layers over fast terrain tiles.

**Wave watch** uses verified 500 m terrain across 16 ridge areas, with nine
locally switched heights from 3,000 to 25,000 ft, signed lift/sink colours,
wind arrows and clickable or dragged vertical cross-sections. This is an
experimental terrain-forced linear estimate, not an exact SkySight forecast.

**Day stars** combine distance, climb, usable height, window and spread-out
risk; 3–5 stars qualify for high-priority email/ntfy alerts. Wave potential
stays separate. **Live OGN Glider Radar** adds fresh public glider reports to
Weather and Task maps, with 15–30 second polling and explicit demo playback.
See [map and analysis methods](docs/map-upgrades.md) for coverage and limits.

## Evaluate without sending

```bash
.venv/bin/python alerts.py --dry-run
```

This retrieves three models for 23 sites and ICON/ECMWF for a 216-point
England/Wales grid, then saves HTML/JSON briefings and a reusable weather bundle
under `var/briefings/`. It evaluates +48 and +120 hours at the enclosing UTC
forecast hour. Reuse recorded data without another API request:

```bash
.venv/bin/python alerts.py --dry-run --snapshot var/briefings/forecast-bundle.json
DUO_DASHBOARD_SNAPSHOT=var/briefings/forecast-bundle.json .venv/bin/python -m streamlit run app.py
```

`--at 2026-10-23T20:00:00+00:00` selects a historical evaluation time for a dry
run. Recorded dashboard mode is labelled, uses stored waypoints for the
inspector, and omits live radar/aviation. Runtime data and secrets are ignored
by Git. `.env.example` lists supported settings; export them explicitly,
because `.env` files are not automatically loaded.

## Automated alerts

[The workflow](.github/workflows/soaring_watchdog.yml) runs `alerts.py --send` at
**00:17, 06:17, 12:17 and 18:17 UTC**, subject to GitHub scheduling delays. It must
be present on the repository's default branch and Actions must be enabled.
Configure repository secrets and variables using [the operations guide](docs/operations.md).
Manual workflow runs default to dry-run mode.

Email uses Gmail SSL on port 465 and strips spaces from the app password.
Free ntfy pushes go to `https://ntfy.sh/g-ckml-soaring-alerts`; subscribe to
`g-ckml-soaring-alerts` in the mobile app. WhatsApp is optional: a blank
CallMeBot key is skipped without blocking Gmail or causing exit 2. Twilio
remains an alternative with an approved template. The SQLite ledger is backed up and restored between GitHub
runners. Missing/corrupt previous state stops delivery; uncertain provider
outcomes require reconciliation. A channel's accepted briefing is suppressed
for the same target local date and 2/5-day horizon. No live notifications were
sent during implementation; provider paths were verified with mocks.

## Methods and data quality

The physics engine computes Hennig cloud base, parcel mixing depth, radiation-
derived convective velocity, moisture sensitivity, spread-out/street flags,
MacCready route capacity, stability/Scorer wave scores and spherical wind
convergence. See [physics methods](docs/physics.md) for equations and thresholds.

The APIs do not provide every requested field. Missing values remain unavailable;
Missing pressure winds are log-interpolated within supported levels. Near-terrain
wind buttons show a labelled surface fallback, never underground wind. Boundary-layer height and heat flux can be estimates, and wave/front
classifications are heuristic. The legacy watchdog routes use endpoint weather; custom tasks sample the
corridor every ≤35 km and integrate hourly climb/glide progress. These are planning aids, not validated predictions
of climb, wave crests or a terrain/airspace-cleared flight plan.

Ridge anchors are approximate named summits. The attributed XCSoar polar is
scaled to the specified wing loadings and is not a measured G-CKML polar.
Use official aviation briefings and the aircraft flight manual for flight
decisions. [Source provenance](docs/data-sources.md), [validation evidence](docs/validation.md)
and the [complete requested specification](docs/specification.md) are retained.

## Data and storage interfaces

`OpenMeteoClient.fetch()` reads one site/model; `fetch_batched()` uses validated
multi-coordinate responses. `Forecast.at()` selects an exact UTC hour and
preserves each field's unit. `available_through` describes non-null surface
temperature coverage, not availability of all pressure fields. Retrieval time
is not a model run time. `AviationWeatherClient.fetch()` retains stale/missing
stations, conditional TAF groups, visibility qualifiers and raw reports.

`DatabaseManager()` creates the local WAL SQLite database on demand.
`save_gold_standard_profile()`, `get_gold_standard_profile()` and
`find_gold_standard_profiles()` support attributed historical JSON profiles,
immutable revisions and deduplication. The delivery protocol and recovery
instructions are in the operations guide. Never place credentials in payloads.

## Network and cloud environment

The saved custom allowlist includes `api.rainviewer.com`,
`tilecache.rainviewer.com`, `tile.openstreetmap.org`,
`a.tile.opentopomap.org`, the weather APIs and notification destinations.
Airspace refresh uses `asselect.uk` and the publisher’s GitHub data; ntfy uses
`ntfy.sh`. BGA synchronization uses `soaringweb.org` and `www.newportpeace.co.uk`;
`members.gliding.co.uk` is retained for source documentation. Leaflet/marker assets use `cdn.jsdelivr.net` from the existing package preset.
The browser also needs tile/script access. Gmail additionally requires outbound
**TCP 465**; an HTTPS proxy domain allowlist alone does not enable SMTP. The
GitHub runner is the intended scheduled delivery host.

Install/start instructions and network changes are saved in the cloud
configuration **draft**. Review and save the changes in environment settings,
then publish the environment to retain them. The committed workflows become available after the corresponding GitHub push;
saving the cloud draft alone does not publish an environment or run a workflow.
