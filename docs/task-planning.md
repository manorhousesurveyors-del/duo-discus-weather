# BGA cross-country task planning

## Catalog and synchronization

The bundled `data/bga_turnpoints.json` contains **1,390** records from the
**2026a BGA list**, supplied by Tim Newport-Peace through the
[Worldwide Soaring Turnpoint Exchange](https://soaringweb.org/TP/BGA/HomePage.html).
Its publisher labels the mirror **unofficial, for flight verification, not
navigation**. These are published BGA identifiers and coordinates, not a
handwritten approximation or a claim of BGA endorsement. The publisher describes
coverage as England, Wales and Scotland; Northern Ireland coverage is not claimed.

The source index identifies the list as amendment 29a to the 1997 list, dated
7 April 2026 / last updated 8 April 2026. Each record preserves the downloaded
CUP fields in `extras`, including findability/airspace warnings where supplied.
Names have their redundant `CODE-` prefix removed. CUP coordinates retain
three decimal places of minutes; feet are converted to metres without rounding
the stored height. `MYN` is published as **Mynd**, 52.518650 N, 2.880950 W,
1,397 ft (425.8056 m). Search also recognizes “Long Mynd”.

Catalog metadata records the source URL, UTC retrieval time, release version,
source-file SHA-256 and normalized-content SHA-256. See
[data/README.md](../data/README.md) for source terms and attribution.

Run a check manually:

```bash
.venv/bin/python bga_turnpoints.py --update
.venv/bin/python bga_turnpoints.py
```

The updater discovers the current CUP download from the mirror's file index
rather than pinning a year-specific filename. The primary Newport-Peace index
is also consulted when available over verified HTTPS. It rejects malformed
records, duplicates, missing heights, fewer than 500 points, implausible UK
coverage, a missing/incorrect MYN, losses exceeding 20% and older named release
versions. Entire downloads validate before an atomic replacement. Failed checks
retain the previous catalog. Unchanged content does not rewrite timestamps or
create empty commits. Large legitimate removals require a reviewed data change.

The **Check for BGA Turnpoint Updates** button writes a local synchronized
catalog in `var/bga_turnpoints.json`. A newer bundled release supersedes an older
local copy. The Actions workflow writes the bundled file on **main** on 1 January,
April, July and October at **06:00 UTC**, and supports manual dispatch. GitHub
may delay schedules. Its token needs repository Contents write permission and
branch rules must permit the bot's direct push; conflicting concurrent pushes
fail safely without force-pushing. This workflow sends no notifications.

The manual uploader accepts UTF-8/BOM and Windows-ANSI SeeYou CUP, comma/tab/
semicolon CSV, normalized JSON and GeoJSON Point FeatureCollections. CUP
latitude/longitude, decimal degrees and separated DMS/DDM are supported, as are
metres/feet and optional runway/frequency/description fields. The waypoint
section ends before related task definitions. An invalid record rejects the
whole import. Uploaded waypoints override matching codes locally and are marked
**user supplied**; remove the local import to return to the BGA catalog.

## Building and exporting

The map starts with **no task route**. Use the sidebar builder or Task planner:

- Search a code or name, choose Append / Start / Turnpoint / Finish, then add it.
  Start/Finish replace endpoints; Turnpoint inserts before the finish.
- Enter a whole code sequence, for example `MYN BIE MYN`, and click Load.
- Select **Add to task** under Map clicks, then click a BGA marker (zoom through
  clusters) or a coordinate. The sidebar's Add as role applies to map clicks.
- Reorder, remove, close at start, set MYN endpoints, or clear the selected day.
  Presets load only after their explicit buttons are clicked.

Tasks persist by local calendar date in `var/tasks/`; `DUO_TASK_STORE` overrides
this directory. This is a single-pilot application: sessions on one deployment
share saved daily plans, and the last save wins. A session retains its in-memory
plan until reopened. Saved coordinates do not silently change after catalog sync.

Exports work even without weather. CUP includes unique waypoint definitions and
the ordered SeeYou task (takeoff/start/turnpoints/finish/landing). XCSoar TSK uses
a racing task, 1 km start/finish lines and 500 m turn cylinders. These are explicit
export defaults: review observation zones, airspace and the source coordinates
in your flight computer. Custom coordinates have unknown elevation until one
is supplied; an unknown elevation is never exported as a fabricated zero.

## What the assessment means

`task_planning.py` samples each great-circle leg at intervals **no greater than
35 km** and fetches the selected model for those coordinates. Every leg has
hourly results from **10:00 through 18:00 Europe/London**. The flight integration
uses the eight intervals ending at 18:00 and checks launch times every 15 minutes.
Times follow BST/GMT; stored forecast axes remain UTC.

It uses the attributed Duo Discus polar fit scaled to **34 kg/m² dry** or
**43 kg/m² ballasted**, conservative net climb, usable mixing/cloud height above
model terrain, and vector along/crosswind components. Adjacent weather samples
use the weaker climb/height. Headwind, crosswind correction and climb drift enter
MacCready ground progress; climb time is included. Cruise airspeed and total task
ground speed are reported separately. The loading-specific polar circling-sink deduction
is inherited from the existing thermal model, not a measured loading-specific
G-CKML climb performance curve.

Progress stops below **500 m usable depth AGL**, at precipitation ≥1 mm/h, or
when lift/wind gives no forward task progress. Shallow depth below 650 m,
spread-out and any precipitation are margin flags. A flight cannot bridge a
missing weather hour, wait through a blocked interval, or sum disconnected
flying periods. Cloud streets are reported but add no assumed speed bonus.
Wave potential is briefed separately and adds no unverified lift to task speeds.

- **FULLY ACHIEVABLE:** a complete modeled flight, at least a one-hour launch
  window, at least 30 minutes finish reserve and no weather margin flags or
  spatial substitutions.
- **MARGINAL / NARROW WINDOW:** a modeled flight exists with limited launch/
  finish margin, weather flags or explicitly approximated stored weather.
- **NOT ACHIEVABLE:** complete required weather does not support a flight.
- **INSUFFICIENT DATA:** missing required weather prevents a reliable verdict.

The weakest leg is the lowest achieved ground speed in the selected flight.
The local day class estimates the best uninterrupted soaring period using
worst-cardinal-direction wind and a 10% distance reserve. Thresholds are 100,
250, 300 and 500 km. It is scoped to the displayed origin, **not nationwide
proof of an achievable remote task**; a candidate route requires its own corridor
assessment. Missing local hours make the class unavailable.

Recorded mode only aliases stored weather within 15 km, labels this approximation
and never promotes it to FULLY ACHIEVABLE. No synthetic fallback is used.
All calculations remain planning estimates: no terrain clearance, airspace
detours, landout options, launch constraints or flight-envelope approval is
established by a verdict.

## Thermal overlay and briefing bot

The thermal overlay is a fixed **net climb in knots** scale: blue below 1 kt,
cyan near 1.7 kt, yellow near 3 kt, orange near 4.25 kt, red from 5.5 kt upward.
It interpolates weather values rather than marker density. Unsupported areas,
sea and areas more than 75 km from samples remain transparent. The same value
keeps its color regardless of zoom or point count. The legend follows visibility.

Toggle it in Map layers or the Leaflet control. **Thermals on by default** stores
an `?thermals=on` / `off` preference in the URL; bookmark that URL to retain it.

The Help & Briefing Bot combines a deterministic task/weather engine with optional
Google Gemini conversation. Without a key it provides the full local briefing.
Try “weather forecast for Denbigh tomorrow”, “why is my task marginal?”, or
“which sector has the best cloud base?”. Explicit distance-search buttons screen
BGA candidates using nearby weather, then check their corridors with the task
engine. Proposed routes display their calculated verdict before loading; loading
geometry does not make an unknown or unachievable route suitable for flight.
A failed bounded search is not proof that every possible task fails.

Sidebar controls, metrics, table headings and markers carry hover explanations.
The bot's explanation cites calculated windows, weakest legs and missing/poor
weather instead of inventing a forecast.

Natural route wording such as “I want to fly from the Mynd to the Mynd North and
Mynd South back to the Mynd” loads `MYN → MNDN → MNDS → MYN`. Ordinary English
words such as “the”, “can” and “fly” cannot match catalog trigraphs. Full names,
codes, Long Mynd/MGC, mndN/mndS and MYN N/MYN S are supported. `MNDN` (52.5550,
−2.8620) and `MNDS` (52.4820, −2.9020) are pilot-defined local ridge references,
not official BGA points; their elevations remain unknown. The published MYN
coordinate is retained. Explicit custom routes load even when weather cannot
establish achievability; that limitation remains in the briefing.

“Tomorrow” means the day after the current local date (snapshot date in recorded
mode), rather than the day after the sidebar selection. Site briefings need no
task: they show hourly surface wind, gusts, site-specific ridge component, cloud
base, thermals, precipitation, upper winds and wave evidence. Ridge support uses
the nearby ridge’s facing direction (Mynd 280°, Denbigh 270°) and a 5 kt on-face
screening threshold. Beyond 20 km of a known ridge, its orientation is unknown. Missing gusts/heights remain unknown. Short West takeoff considerations
require current site conditions and the duty instructor's decision.

Set `GEMINI_API_KEY` in Streamlit Secrets or the server environment; a password
input under **Gemini settings** overrides it for that session. **Forget pasted
key** removes the runtime value. Never commit credentials. `gemini_rest.py` uses
standard-library verified HTTPS, so it does not depend on a Google SDK install.
API keys use Google's `?key=` parameter; bearer credentials use `Authorization`.
Auto detection tries the other method on authentication errors. An AQ prefix
alone does not prove validity or access to the Gemini API.

**Check connection / refresh models** queries the paginated account model list.
Only available text `generateContent` models appear. Newer versions rank first;
requested names such as `gemini-3.8-flash` appear only if returned by Google.
Generation falls back across discovered models on 400/403/404; quota failures
stop promptly. The badge shows the connected/last successful model or the HTTP
reason, with credentials redacted. Listing access does not establish generation
quota. Questions, recent conversation, forecasts and waypoint context are sent
to Google when a configured key is used. The app checks proposed codes and task
weather, then offers **Load suggested route onto map** with the verdict visible.

Typed questions always use the free-form briefing path. Only the explicit
**Suggest 250 km / Suggest 300 km** buttons run the bounded candidate search.
Lowercase ordinary words never match three-letter codes; explicit uppercase
trigraphs and full waypoint names still work. Described route commands retain
the direct-load behaviour above. AI route proposals require a load click.

Local forecasts use a **20 km sector** by default. Place resolution checks BGA
names/codes and all UK airfields, including club aliases, before using the UK
Open-Meteo/GeoNames geocoder. A clicked coordinate is used for “here”. Duplicate
place names need a county or a map point; lookup errors never silently substitute
Long Mynd. Cotswold resolves to the published Cotswold Airport coordinate.

A local answer contains a site summary, 09:00–18:00 Europe/London hourly table,
cloud base AMSL and AGL, usable thermal window, spread-out flags, site-specific
ridge and wave evidence, and launch/landing considerations. Live requests fetch
the centre and four points on the sector boundary. Missing hours remain unknown;
recorded forecasts outside 20 km cannot fill a local gap. Regional screening uses
its separately labelled, coarser 75 km coverage only when explicitly requested.

National region tables, five-day regional lists, national circuits and old task
legs are excluded from local context and answers. Five-day local outlooks require
an outlook request; task and circuit analysis require explicit task/circuit
wording. Local Gemini requests also exclude unrelated conversation history and
route proposals. Model discovery is cached for one hour per credential fingerprint
and authentication mode; the refresh button checks immediately. Pasted keys stay
in session memory when switching pages and can still be forgotten explicitly.

The horizontal navigation mounts only the active page. Text pages build no maps.
Static catalogs and terrain arrays are shared read-only server resources; forecast
products and day summaries are cached for 30 minutes. Display coordinates use four
decimal places; exported official points and obstacle geometry retain full source
precision. Compressed map documents keep the initial payload small; subsequent
point edits carry only canonical task state.

`active_task_points`, `task_version` and a session/date envelope are the shared
Python state. Both maps use browser key `duo_active_task_v1`. Adds, magnetic drops,
midpoint insertions and Undo save immediately through a Streamlit fragment. Mouse
movement and panning send no updates. Clear Task removes the route and browser
key, invalidates old analysis/exports and refreshes the active page. Late versions
are rejected, so stale browser messages cannot restore a cleared task. Daily task
files remain the persistent archive. Finish/Analyse refreshes weather tables after
edits; the live map geometry updates before those calculations.

A map click supplies the bot's selected point. Wave/traffic selections send a small
metadata update; route clicks include it with the task state. Route editing uses
18 px snapping, numbered draggable points and + insertion handles. Closed
Start/Finish endpoints remain linked.
