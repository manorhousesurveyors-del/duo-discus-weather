# UK map coverage, airfields and route analysis

## Responsive maps and wind coverage

All three map components use Leaflet canvas rendering and return exactly
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
in the map. Clicking refreshes the server-cached NOAA reports (60-second TTL)
and reopens the selected airfield's popup. Reports include runway/elevation,
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
Click Start, subsequent points, then **Finish here** or **Return home**. Undo
and Clear update only that date's route. CUP import requires exactly one
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

Fourteen ridge anchors include Denbigh/Lleweni Parc (Clwydian ridge) and
Plynlimon/Cambrian Mountains. Buttons select 3,000 / 10,000 / 12,000 / 16,000 /
20,000 / 25,000 ft AMSL. All six layers are prepared together and switched locally.
Pressure profiles now include 400 and 300 hPa to bracket higher altitudes.

The overlay uses positive model vertical motion at the selected height, gated
by stable cross-ridge flow, interpolated around supported ridge sites. Colours
progress through yellow, orange, red, dark red and purple; unsupported areas
stay transparent. This is interpolated wave potential, not resolved lift over
every mountain. No model vertical motion means no positive lift estimate.

Three downwind guides are oriented across the selected-height wind, spaced
from the Scorer wavelength (bounded 2–30 km). Labels show lower/upper Scorer
parameters and evidence completeness. They are indicative primary/secondary/
tertiary guides, not predictions of exact crest positions or usable climb.
