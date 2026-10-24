# UK map coverage, airfields and route analysis

## Responsive maps and wind coverage

Weather, Task, Wave and Traffic maps use Leaflet canvas rendering and return exactly
`last_object_clicked` and `last_clicked` to Streamlit. Panning, zooming and
wind/wave altitude selection do not call Python. Views persist separately in
browser session storage. A click bridge preserves the selected waypoint or
command inside the click object. Expensive wind frames, contour geometry and
wave images are cached in `st.session_state`. Each map is built when its tab opens.

Wind uses a complete 13 × 15 grid (195 nodes), 49.8–59°N and −8.5–2°E. There is
no coastline or distance-cutoff mask. Each pixel reads four enclosing nodes.
Missing 950/800 hPa winds use vector interpolation in log pressure, including
supported near-surface levels. A pressure level at or below the lowest near-surface sample uses an explicitly
labelled surface fallback. Missing source data and time horizons remain missing;
UKV's shorter forecast cannot establish a five-day upper-air forecast.

The first national request can take minutes due to the free API's quota.
Once available, frames are reused locally; fast interaction does not imply
instant downloads. Soaring contour modes retain their separate land grid and
support limits, because they must not imply thermal flight over water.

## Airfield reports

The attributed dataset contains 1,098 airfields and 130 unique NOAA reporting
station codes. Turquoise markers identify gliding fields, purple reporting
stations, blue other fields and grey former gliding sites. Layer controls can
hide them. See [data provenance](../data/README.md).

Hover fetches Open-Meteo current surface weather on demand, cached five minutes
in the map. Clicking opens a browser-local popup containing the current surface
request and the NOAA reports bundled when the map was built. NOAA server
requests have a 60-second TTL; rebuilding the map refreshes that bundle. Reports include runway/elevation,
wind/gust, temperature/dewpoint, mean sea-level pressure, visibility and a
six-hour model trend. Temperature/dewpoint gives an estimated cloud base;
it is not a measured ceiling. Live surface reports use Open-Meteo's current
best-match feed independently of the sidebar forecast hour/model.

Reporting fields show their METAR and full TAF. Other fields show explicitly
identified nearby station reports and distance. Missing/stale/expired reports
are labelled. The popup does not confirm runway serviceability or permission.
Recorded mode disables live airfield requests. Browser access to Open-Meteo
is required; NOAA is fetched server-side because its API does not allow CORS.

## Task map, imports and analysis

The dedicated Task planner map shows all 1,390 BGA points and the airfields.
Canvas viewport culling replaces thousands of marker objects. Fast Esri terrain
is the default; OpenTopoMap remains optional. Airspace display geometry alone
is simplified by approximately 100 m; obstacle tests retain every original
boundary. STF, PFD and thermal layers draw below the waypoint/airfield canvases.

Click Start and watch the dashed leg follow the cursor with km/°T labels.
Each point locks in a leg. Clicking Start again, **Finish here** or **Return
home** hides the dashed line immediately and makes one explicit save request.
**Analyse / save** and **Optimise** also deliberately call the server. Other
clicks, Undo, Clear, hovering, layer changes and panning remain browser-local.
A per-day session-storage draft survives iframe rerenders while its saved base
route is unchanged. Tables and exports describe the last saved route until
the draft is committed. Clearing requires Analyse / save to persist. CUP import requires exactly one
Related Tasks declaration; a waypoint list has no reliable route order and is
rejected. XCSoar TSK import requires Start and Finish. Import retains waypoint
sequence/coordinates, not arbitrary instrument-specific observation-zone rules;
review zones before exporting the application's documented defaults.

**Analyse Task & Route Weather** fetches corridor forecasts and reports the
hourly task verdict, launch window, STF, XC speed and leg time. It includes
cloud base AMSL/AGL, conservative climb, along-track wind, and rain/spread-out
flags. Convergence is a local surface-wind gradient fitted from non-collinear
samples, reported in 10⁻⁴ s⁻¹; it is not proof of a resolved thermal street.

A dynamic-programming search compares centre and ±10 km candidate sample paths
at the selected hour, using the selected Duo polar and local weather. Green
dashed lines show the lowest modelled elapsed time among those candidates.
Missing weather, shallow lift, rain, water samples and supplied airspace
intersections exclude candidates. No assumed convergence/wave lift bonus is
added. This is a bounded weather routing suggestion, not an obstacle-cleared
route or a global optimum. The separate task flight calculation integrates
changing hourly weather; the candidate line is a selected-hour comparison.

Terrain comes from Open-Meteo elevation, sampled every ≤5 km along track and
at ±2 km laterally. The table compares the sampled maximum with the best
hourly corridor cloud base. This is an optimistic envelope, not guaranteed
clearance at the actual crossing time; narrow peaks, masts, rotor and an energy
line outside that corridor are not cleared. Missing DEM samples stay unassessed.
Coastal flags indicate water or coastline proximity within 15 km; sea-breeze
position and sink intensity are not resolved by this proximity check.

Bundled ASSelect snapshots enable horizontal route intersection checks within
5 km, retaining class, lower and upper limits. The latest AIRAC set effective
on the selected forecast date is used, with a warning if it is expired. An
explicit OpenAir/GeoJSON upload overrides the bundled data. OpenAir polygons, circles and
common arc forms are supported. Supplied geometry is also an optional map layer.
The candidate energy-line search conservatively excludes all intersected
polygons without inferring vertical clearance. See [source hashes, dates and refresh commands](../data/README.md).
With no boundary file, airspace
is **UNASSESSED**. Nearby large/military aerodromes within an advisory 20 km
radius are listed separately; those radii are not actual airspace boundaries.
Current NOTAM activation and legal clearance need official sources.

## Wave Watch

A single terrain grid covers 50–59°N, −8–2°E at approximately 2 km spacing.
Regional atmospheric columns force the full grid, with smoothly blended signed
solutions and no internal site clipping boxes. BGA turnpoints and airfield dots
start hidden; the Layers control can show them. Airspace uses thin boundaries.

Nine buttons select 3,000 / 7,000 / 10,000 / 12,000 / 13,000 / 16,000 / 20,000 /
23,000 / 25,000 ft AMSL. All raster layers, wind vectors and section samples are
bundled and decoded once. Altitude switches, pan/zoom, hill clicks and dragged
cross-sections need no forecast requests or Python reruns. Each badge gives the
peak positive estimate among grid cells in the current view.
It is not the strength at every point in that height band.

Blue/cyan show sink; pale yellow through purple show lift. Values between
−0.35 and +0.35 kt are transparent, with a smooth alpha transition towards
±0.5 kt. Below-ground cells and unsupported data are also transparent.
Regular black arrows show flow towards the wind's destination. A hill click
selects 20 km upwind to 70 km downwind; Draw Wave X-Section permits any dragged
track. The chart spans the surface to 25,000 ft, samples terrain at 1 km and
interpolates the nine solved levels. Unsupported heights remain grey.

The approximately 2 km terrain spacing does not make the coarse atmospheric forecast equally
precise. The damped linear Fourier/WKB model cannot establish exact crests,
rotor, nonlinear breaking, usable climb or a safe entry point. It is an
independent diagnostic, not SkySight data or a replica of its forecast model.
See [physics](physics.md) and [elevation provenance](../data/README.md).

## Live OGN gliders

**Live OGN Gliders (FLARM)** appears in Weather and Task layer menus; the
**Live OGN Glider Radar** tab enables it initially. A shared server worker reads
OGN/SpotTheGlider's HTTPS UK snapshot every 20 seconds and publishes a same-origin
static JSON file. Browser timers poll at 15, 20 or 30 seconds without rerunning
Streamlit. Public device metadata supplies registration/CN and highlights G-CKML.

Reports must be type 1 gliders, inside the UK bounds, at most 120 seconds old and
moving at least 25 km/h. Movement is only an airborne proxy. Device registration
privacy flags are respected; unverified identities are anonymous. Headings rotate
canvas icons; popups convert altitude to feet and speed/vario to knots. Vario is
GNSS-derived, not a total-energy/netto instrument measurement.

Snapshots older than 60 seconds and reports older than two minutes are hidden,
even with refresh paused. No reports or a provider failure never substitutes
fake live aircraft. **Demo** explicitly shows seven fictional gliders and can
be switched back to Live. Recorded weather disables live traffic requests.
OGN reception is incomplete and unsuitable for collision avoidance.

## Optional Point Forecast Probe

On **Weather map**, turn on **Enable Point Forecast Probe** above the map.
Click a coordinate, a visible BGA marker, or an airfield. **Pick another point**
hides the chart while keeping the crosshair active. **× Close / Turn Off Probe**
turns the switch off too. Leaving Weather always resets the switch.

The chart shows 07:30–18:30 on the selected date and the next local date, including
BST/GMT transitions. Scroll sideways for the second day and down for the hourly
strips. The white hourly grid keeps its 165 px label column fixed while scrolling.
Each date includes calculated sunrise/sunset at the clicked location (approximate
astronomical times, not a prediction of sunlight through cloud). The numerical
table beneath the canvas provides accessible values.

- The lower 7,000 ft receives extra vertical space; the scale reaches 38,000 ft.
- Clouds are a relative-humidity/model-cover proxy. Hatching means missing data.
- Indigo, blue, cyan and mint contours show conservative net thermal strength
  below the estimated thermal top.
- Rose is the LCL: terrain feet + `400 × max(0, T₂m − Td₂m)`.
- Solid black is the estimated thermal/mixing top. Stacked scalloped puffs fill
  positive thermal-top minus LCL depth; deep stacks show deeper possible cloud
  and high low-cloud cover darkens the stacks. These are model cloud estimates,
  not confirmation of towering cumulus or a thunderstorm prediction.
- The bordered **Soarable Height** rows exclude cloud depth. An unsoarable hour
  displays terrain in the MSL row and zero AGL; an unknown hour displays dashes.
- Separate temperature/dewpoint rows align with the white line chart. **Nearest
  Obs** uses already-loaded METARs (for example from the METAR overlay). Actual
  reports appear as red dots; multiple reports no more than three hours apart
  can be joined with dashed red segments. With no cached report, the chart says
  unavailable and does not fabricate observations or a blended forecast.
- Wind barbs use 5/10/50 kt conventions. Surface wind is **10 m AGL**; the source
  does not supply 2 m wind. Temperatures/dewpoints are at 2 m.
- Wind and thermal-top wind have separate bordered cells with direction arrows,
  speeds, bearings and bold surface gusts. Heating cells turn white to yellow.
- The pressure curve spans 980–1020 hPa. Arrows flag values outside that range;
  numerical readouts retain the actual pressure.

Clicks use only cached data. The clicked position is retained, and up to four
nearby samples (within 120 km) are interpolated with distance weights. Wind is
interpolated as east/north vectors. Half-hour values require both adjacent hourly
samples. Marker elevation is used when known; otherwise model terrain is
interpolated and labelled. These are model-grid estimates, not point observations.

The summary shows retrieval time because Open-Meteo does not supply the model's
initialisation time in this response. Missing forecast days or pressure levels
are not extrapolated. The standard cached profiles now include 250 and 200 hPa,
so new responses can bracket the upper part of the plot. Existing older snapshots
continue to show unavailable upper bands until refreshed.
