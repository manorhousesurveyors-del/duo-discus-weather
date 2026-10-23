# User master specification

Act as a Principal Systems Architect, Senior Python Developer, and Meteorological Physics Engineer. We are building a bespoke, full-stack gliding, cross-country soaring, and general aviation meteorological platform and automated alerting engine tailored for flying an original model Schempp-Hirth Duo Discus (UK registration G-CKML) across the United Kingdom.
Please read and lock in the following system architecture in its entirety. Do not summarise or skip details. Because this is a substantial application, we will build it in five sequential, complete Python modules without placeholders or truncated code.
MODULE 1: CONFIGURATION AND TOPOGRAPHICAL DATABASE (`config.py` and `database.py`)
1. Define the geographic monitoring zones and coordinates across the UK: Shropshire (Midland Gliding Club / Long Mynd: 52.518, -2.880), Snowdonia (53.068, -4.076), Herefordshire (52.240, -2.880), South Wales (Black Mountains / Talgarth: 51.977, -3.133), Oxfordshire (Didcot: 51.606, -1.241), Cambridgeshire (52.205, 0.121), Lincolnshire (53.230, -0.540), South Yorkshire (53.522, -1.131), and Humberside / Goole (53.704, -0.870).
2. Define the two primary cross-country task templates: Route A (Long Mynd to Goole, south to Didcot, returning to Church Stretton) and Route B (Snowdonia to South Wales transit).
3. Define a dictionary of the 12 primary UK mountain wave ridges with their exact latitude, longitude, crest elevation in feet, and perpendicular wind azimuth (in degrees): Long Mynd (280/100 deg), Snowdonia (250/070 deg), Berwyn Range (240/060 deg), Black Mountains (310/130 deg), Malvern Hills (270/090 deg), Peak District Mam Tor (260/080 deg), Yorkshire Dales (250/070 deg), Pennines Cross Fell (240/060 deg), Lake District (250/070 deg), Cheviots Milfield (260/080 deg), Scottish Borders Portmoak (230/050 deg), and Cairngorms Aboyne (270/090 deg).
4. Define the ICAO airfield list for live METAR/TAF polling: EGOS, EGNR, EGCV, EGBB, EGBJ, EGBS, EGOV, EGFF, EGVN, EGTK, EGSC, EGTC, EGNJ, EGXW, EGXC.
5. Store the Schempp-Hirth Duo Discus polar polynomial coefficients (dry wing loading ~34 kg/m2 and ballasted ~43 kg/m2, max L/D ~45:1, circling sink rate in a 40-degree bank ~1.6 knots) to compute MacCready cross-country speeds for 250 km, 300 km, 500 km, and 750 km task viability.
6. Create an SQLite database manager (`database.py`) to log sent 5-day and 2-day alerts (preventing duplicate spam) and to store historical "Gold-Standard Day" atmospheric profiles.


MODULE 2: DATA INGESTION PIPELINE (`data_ingestion.py`)
1. Build an asynchronous or multi-threaded Python fetcher querying the free Open-Meteo API (`[https://api.open-meteo.com/v1/forecast](https://api.open-meteo.com/v1/forecast)`).
2. Query the UK Met Office 2km (`ukmo_uk_deterministic_2km`) model for 0-48 hour high-resolution forecasts, the DWD `icon_eu` 7km model for 0-120 hour (5-day) forecasts, and ECMWF `ifs025` for medium-range verification.
3. Request surface and boundary variables: `temperature_2m`, `dew_point_2m`, `surface_pressure`, `pressure_msl`, `cloud_cover`, `cloud_cover_low`, `cloud_cover_mid`, `cloud_cover_high`, `precipitation`, `precipitation_probability`, `shortwave_radiation`, `direct_radiation`, `cape`, `lifted_index`, `boundary_layer_height`, `wind_speed_10m`, `wind_direction_10m`, `wind_gusts_10m`, and `soil_moisture_0_to_7cm`.
4. Request pressure-level variables across 1000, 950, 925, 850, 700, 600, and 500 hPa: `temperature`, `relative_humidity`, `wind_speed`, `wind_direction`, `geopotential_height`, and `vertical_velocity`.
5. Build a live aviation poller querying the NOAA Aviation Weather Center API (`[https://aviationweather.gov/api/data/metar](https://aviationweather.gov/api/data/metar)` and `/taf`) for the UK ICAO stations in JSON format, extracting live wind vectors, gusts, visibility, cloud bases, and QNH.


MODULE 3: ATMOSPHERIC PHYSICS AND SOARING ENGINE (`soaring_physics.py`)
1. Thermal and Cloud Base Evaluator: Calculate Cumulus Cloud Base in feet AMSL using Hennig's spread formula (`((Temp_2m - Dewpoint_2m) * 400) + terrain_elevation_ft`) cross-checked against `boundary_layer_height`. Calculate convective velocity scale (w*) from boundary layer height and sensible heat flux (estimated from `direct_radiation` penalised by `soil_moisture_0_to_7cm` Bowen ratio damping). Convert to knots and deduct 1.6 knots for Duo Discus circling sink to output net thermal climb rate. Explicitly flag whether net climbs reach 3.5 to 4.5 knots and whether cloud base exceeds 4,500 to 5,000 feet AMSL.
2. Overdevelopment and Cloud Street Analyser: Flag overdevelopment (spread-out) risk if relative humidity at the pressure level nearest `boundary_layer_height` exceeds 85% while low cloud exceeds 60%. Flag Cloud Street alignment if boundary layer wind (925-850 hPa) is 15-25 knots with less than 20 degrees of directional shear between surface and inversion.
3. Duo Discus Task Viability Calculator: Using the MacCready ring math for the Duo Discus polar, calculate achievable groundspeed along each leg of Route A (Long Mynd-Goole-Didcot-Long Mynd) and Route B (Snowdonia-South Wales) for every hour between 10:00 and 18:00 BST, outputting whether the day supports a 250 km, 300 km, 500 km, or 750 km task.
4. National UK Mountain Wave Predictor: For all 12 UK wave sites, compute an hourly Wave Score (0-100) by checking: (a) 925/850 hPa wind speed >= 18 knots within 30 degrees of the ridge perpendicular azimuth; (b) directional shear < 30 degrees between 900 hPa and 500 hPa; (c) static stability (Brunt-Vaisala frequency / temperature inversion between 850 and 700 hPa) and decreasing Scorer Parameter ($l^2$) with height; and (d) direct model `vertical_velocity` converted from Pa/s to knots at 850 and 700 hPa. Rank the top wave sites in the UK for any given hour.
5. National UK Convergence Detector: Across a 2D coordinate grid covering England and Wales, convert 10m and 950 hPa wind speed and direction into zonal ($u$) and meridional ($v$) vectors, and compute horizontal wind convergence ($-(\partial u/\partial x + \partial v/\partial y)$). Identify sea-breeze fronts and synoptic convergence lines where convergence exceeds threshold values and coincides with a dewpoint gradient.


MODULE 4: AUTOMATED 5-DAY AND 2-DAY ALERTING WATCHDOG (`alerts.py`)
1. Build an automated evaluation routine designed to run every 6 hours via task scheduler or cron job.
2. Inspect forecasts at T-120h (5 days ahead) and T-48h (2 days ahead). Trigger an alert if any of the following conditions are met: (a) Local or Cross-Country Thermal Day where predicted thermal climbs reach >= 3.5 knots and cloud base >= 4,500 ft AMSL across Shropshire or the task routes; (b) Wave Day where any UK site scores >= 75/100; or (c) Strong Convergence Line detected across the Welsh Marches, Pennines, or Southern England.
3. Dispatch a formatted HTML briefing email to `manorhousesurveyors@gmail.com` via `smtp.gmail.com` (port 465 using SSL and an environment variable App Password) containing the regional table, route viability verdict (250k to 750k), wave rankings, and convergence summary.
4. Dispatch a concise WhatsApp alert to mobile number `+447976125887` supporting both the free CallMeBot WhatsApp webhook API and the Twilio WhatsApp API (configured via environment variables).


MODULE 5: SKYSIGHT-STYLE INTERACTIVE MAP AND DASHBOARD (`app.py`)
1. Build a browser-based interactive dashboard using Streamlit and Folium / PyDeck (MapLibre GL) paired with Plotly for atmospheric soundings.
2. Include an interactive hour-by-hour time slider (09:00 to 19:00 BST) and day selector (Day 0 to Day 5).
3. Render toggleable map layers over UK topographic tiles: (a) Interpolated Thermal Strength Heatmap (knots); (b) Convective Cloud Base Heatmap (feet AMSL); (c) Convergence Line Ribbons; (d) Top UK Wave Site Markers and Lee Wave Bars; (e) Live METAR station wind barbs and colour-coded flight rules; (f) Duo Discus Route Polygons (Long Mynd-Goole-Didcot and Snowdonia-South Wales); and (g) Live RainViewer weather radar tile overlay (`[https://api.rainviewer.com/public/weather-maps.json](https://api.rainviewer.com/public/weather-maps.json)`).
4. Provide a sidebar click-inspector that plots a live interactive Skew-T / Tephigram sounding and hourly meteogram for any selected waypoint or coordinate.


Please confirm you have ingested this complete specification, and begin immediately by writing and saving the complete, production-ready files for Module 1 (`config.py` and `database.py`) and Module 2 (`data_ingestion.py`) directly into the repository workspace. Wait for my confirmation before generating Module 3.
