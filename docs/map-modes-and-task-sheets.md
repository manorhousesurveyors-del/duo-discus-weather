# Interactive map modes and task sheets

The floating **Map modes** button starts collapsed. Choose **Off / Clean Map**
to hide weather layers. An explicitly selected task remains saved and visible.
The sidebar offers the same mode selector for keyboard users. Older cloud-base,
convergence, wave and METAR layers remain under **Other weather layers** and
**More layers and click settings**. A bookmarked `?thermals=on` starts the
new thermal contour mode.

These are independent visualisations inspired by Windy and SkySight. They do
not embed those services or use their proprietary forecasts. No reference
screenshots were available during implementation, so visual equivalence to a
particular screenshot has not been established.

## Wind

Wind mode fetches a paced, cached 40-point UK land grid plus the existing regional,
ridge and active-task forecasts. National samples are about 85 km apart; the
interpolated display is not a higher-resolution forecast. Blue/green through
yellow/red/magenta shows speed in knots. White canvas particles follow the wind
towards its destination. The animation honours reduced-motion preferences.

The six-level bar selects surface 10 m, 950, 850, 800, 700 or 500 hPa. Pressure
heights are approximate standard-atmosphere labels, not terrain clearance.
Missing pressure levels use vector/log-pressure interpolation only when
bracketed by above-terrain levels. Levels below terrain stay unavailable.

The wind timeline has 120 hourly instants beginning at the current local day's
midnight. UTC increments preserve the repeated autumn hour. The wind timeline
is independent of the sidebar's task/sounding hour; its own label is authoritative.
Shorter model coverage remains blank. Point flags show spatially interpolated
model wind, an expandable six-level profile and a five-day timeline. They are
not station observations at the clicked location.

## Soaring contours

Contours use black-outlined, filled bands from purple/dark blue through cyan,
green, yellow, orange and red. They are interpolated inside the sample hull,
within 120 km of support, and clipped to land. Known zero values, non-soarable
cells and missing coverage remain clear. A clear map does not prove good weather.
Negative net climb is marked as sink at its sample location.

- **Thermal Lift / Sink:** conservative net thermal climb, in knots. Cells blocked
  for modeled XC flight remain clear even if the parcel estimate is positive.
- **Potential Flight Distance:** each sample's local 10:00–18:00 capacity, using
  the existing height/rain thresholds, worst cardinal-direction wind and 10%
  reserve. It cannot certify a 500 km corridor starting there.
- **XC Speed:** conservative local progress including climb time and wind.
- **Speed to Fly:** MacCready cruise airspeed in knots, averaged across four
  cardinal tracks using the selected 34/43 kg/m² polar. A particular leg has
  its own wind-corrected values in the planner and task sheet.

Hover/click flags show both XC speed (km/h and kt) and STF (kt). Flags identify
how far the displayed source sample is from the click; contour values between
samples are estimates.

## Radar and calibrated rain rates

RainViewer's current personal-use API supplies observed past PNG radar, Universal
Blue colours and maximum native zoom 7 (512 px). This was confirmed against its
[public example repository](https://github.com/rainviewer/rainviewer-api-example).
The app does not label enlarged public tiles as HD or invert image colours into
invented mm/h. In default radar mode, the requested colourful mm/h legend is
explicitly a **reference scale**, and point flags say **rate unavailable**.

A separately supplied calibrated feed can enable the requested quantitative
colour ramp and point rates. Set `DUO_RADAR_RATE_GRID_URL` to a credential-free
HTTPS JSON endpoint (without query/fragment), allow its host if necessary, and
restart the app. No such feed is bundled or provisioned. Contract:

```json
{
  "schema_version": 1,
  "units": "mm/h",
  "observed_at": "2026-10-24T00:00:00Z",
  "source": "Your radar provider attribution",
  "bounds": [[50, -5], [56, 1]],
  "values": [[0, 0.6, 3], [12, null, 200]]
}
```

Bounds describe outer cell edges. Rows run south to north, columns west to east.
Arrays must be rectangular, 2–512 cells per dimension; each value is a finite,
nonnegative mm/h rate or `null` for unavailable coverage. Zero means an observed
dry cell. Images are reprojected into Mercator; clicks read the containing source
cell. The legend and callouts include source/time. Provider precision and freshness
remain the provider's responsibility. Future timestamps beyond ten minutes,
unknown units and malformed grids are rejected.

## Route completion and optimisation

Open **Route Forecast Planner**, zoom in and click BGA markers in sequence.
Click **Finish here**, **Return home**, or the starting BGA point again to complete
a route. **Undo last** and **Clear route** are immediate. Completed closed routes
also open their sheet when loaded through the text builder. Other edited routes
can be completed with **Complete route & open task sheet**.

Leg labels show distance and true bearing. Assessment still samples every 35 km
or less, checks every hour and integrates 15-minute launch candidates. Actual
integrated leg times, cruise speed and wind are retained for the task sheet.

**Optimise Route** accepts 200/300/500 km or a custom 50–1000 km target. It screens
up to 30 BGA triangles/out-and-returns using nearby weather, then fetches/checks
up to three shortlisted corridors. Routes fall between the target and 108% of it.
Adequate weather margins rank first; the chosen objective then ranks speed or
distance. The winning complete flight is plotted and its sheet opened. When no
flight is established, the existing task is preserved. This is a bounded search,
not proof of the globally fastest route or proof that no other route is possible.

## Task sheet and downloads

The sheet includes G-CKML, date, loading, distance class, verdict, elapsed time,
launch windows and task speed. Waypoint and leg data include DM.m coordinates,
source elevation, km/nm, true/magnetic tracks, wind, corrected true glide heading,
STF and actual integrated leg elapsed time. Unknown flight quantities remain
unavailable. Magnetic variation uses WMM2025 at the leg start; no extrapolation
outside model validity is permitted. Wind and heading are leg averages.

The route overview is an offline geographic snapshot with land mask and task
labels. It does not contain airspace or terrain heights. Published airfield and
outlanding waypoint candidates within 15 km of each leg are listed; permission,
runway condition and suitability have not been verified.

Both instrument exports declare the same planning defaults:

| Point | Declared zone |
|---|---|
| Start | 5 km line |
| Turn | BGA fixed-course 20 km sector plus 0.5 km barrel |
| Finish | 1 km cylinder/ring |

CUP uses the standard **Related Tasks**, **Options** and **ObsZone** records;
it does not contain XML. TSK uses an XML **Task** with **BGAFixedCourse** turn
zones. Formats were checked against XCSoar's parser/serialiser source and
structural tests; import on actual Oudie/SeeYou/XCSoar hardware was not available.
Always check imported points, zones and event rules on the target instrument.

PDF is A4 landscape with a route overview and leg tables. Word contains editable
headings, tables and briefing notes. PDF, Word, CUP and TSK work even when a
forecast cannot establish a flight, with unavailable values clearly marked.

Direct CUP/TSK links use Streamlit static serving, enabled in `.streamlit/config.toml`.
Generated files are ignored by Git and stored at `static/tasks/<random token>/`.
QR codes encode real HTTP(S) file URLs, not data URLs. They use the browser-visible
app address, or `DUO_PUBLIC_BASE_URL` for a reverse proxy/path prefix. The task
sheet's address setting can override it. A phone must reach the same server;
localhost on a PC is not reachable from a different device.

Anyone possessing a link can fetch that task. Files older than 30 days are
removed when another export is generated. Hosting restarts with ephemeral disks
may remove files sooner. No task data is uploaded to a third-party QR service.

## Help Guide

The prominent tab contains 18 short step cards: quick start, colour keys, map
planning, wing loading, downloads and gliding terms. It uses 18 px sans-serif
text, 1.75 line height, left alignment, extra word/letter spacing and warm cream /
pastel cards, informed by BDA guidance. Its Weather Question Bot answers from
the current task/day in numbered, plainly worded steps. No external AI key is
needed; unknown questions receive guidance about supported topics.
