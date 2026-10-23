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

The dashboard provides nine regional forecasts, twelve wave sites, thermal and
cloud-base maps, convergence ribbons, route capacity, observed radar, METAR/TAF
popups, and a map-click sounding/meteogram inspector. Choose model, day, local
hour, wing loading and layers in the sidebar. Times follow **Europe/London**,
including winter GMT. The initial view selects tomorrow at 13:00 after today's
13:00 has passed. Refresh weather explicitly to request new data; cached
forecasts last 15 minutes and aviation observations 60 seconds.

Public weather APIs need no credentials. The first map/grid load can take a few
minutes because requests respect Open-Meteo's weighted quota. Allow about 2 GB
RAM for the dashboard and land mask; the mask alone expands to about 1 GB.
Dependencies are pinned in [requirements.txt](requirements.txt).

## Evaluate without sending

```bash
.venv/bin/python alerts.py --dry-run
```

This retrieves three models for 21 sites and ICON/ECMWF for a 216-point
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

Email uses Gmail SSL on port 465. WhatsApp supports CallMeBot or an approved
Twilio template. The SQLite ledger is backed up and restored between GitHub
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
900/950 hPa interpolation is explicit and stays inside available pressure
levels. Boundary-layer height and heat flux can be estimates, and wave/front
classifications are heuristic. Route capacity uses endpoint weather and an
idealized climb/glide cycle. These are planning aids, not validated predictions
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
Leaflet/heatmap assets use `cdn.jsdelivr.net` from the existing package preset.
The browser also needs tile/script access. Gmail additionally requires outbound
**TCP 465**; an HTTPS proxy domain allowlist alone does not enable SMTP. The
GitHub runner is the intended scheduled delivery host.

Install/start instructions and network changes are saved in the cloud
configuration **draft**. Review and save the changes in environment settings,
then publish the environment to retain them. Saving files here does not push
them to GitHub or activate its schedule.
