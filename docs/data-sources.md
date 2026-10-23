# Reference data and provider contracts

## Architecture inputs

The nine monitoring coordinates, reciprocal ridge wind azimuths, 15 ICAO codes,
alert recipients, task distances and estimated 1.6 kt circling sink are from the
user's [master specification](specification.md). The circling sink estimate is
not inferred from the straight-flight polar. The recipients are defaults for
later configuration; Modules 1 and 2 do not send messages.

The specification describes Route A returning to Church Stretton in Module 1,
then explicitly to Long Mynd in Module 3. The stored closed route returns to the
Long Mynd coordinate above Church Stretton. Route B is one-way. These geographic
templates do not define official competition turnpoints, airspace clearance,
terrain clearance or landable fields.

Named mountain regions are not single ridge crests. The following representative
summit anchors are approximate and await authoritative terrain/ridge validation.
They are deliberately marked `survey_verified=False`; numerical precision is
not evidence of survey accuracy. Elevations are stored in feet via the exact
conversion 1 ft = 0.3048 m.

| Specified ridge/site | Representative summit | Latitude | Longitude | Elevation m | Wind from ° |
| --- | --- | ---: | ---: | ---: | --- |
| Long Mynd | Pole Bank | 52.5185 | -2.8805 | 516 | 280 / 100 |
| Snowdonia | Yr Wyddfa | 53.0685 | -4.0763 | 1085 | 250 / 070 |
| Berwyn Range | Cadair Berwyn | 52.8806 | -3.3807 | 832 | 240 / 060 |
| Black Mountains | Waun Fach | 51.9620 | -3.1431 | 811 | 310 / 130 |
| Malvern Hills | Worcestershire Beacon | 52.1044 | -2.3394 | 425 | 270 / 090 |
| Peak District | Mam Tor | 53.3491 | -1.8091 | 517 | 260 / 080 |
| Yorkshire Dales | Ingleborough | 54.1663 | -2.3977 | 723 | 250 / 070 |
| Pennines | Cross Fell | 54.7025 | -2.4861 | 893 | 240 / 060 |
| Lake District | Helvellyn | 54.5268 | -3.0161 | 950 | 250 / 070 |
| Cheviots / Milfield | The Cheviot | 55.4788 | -2.1478 | 815 | 260 / 080 |
| Portmoak | Bishop Hill | 56.2180 | -3.3330 | 461 | 230 / 050 |
| Cairngorms / Aboyne | Lochnagar | 56.9590 | -3.2450 | 1155 | 270 / 090 |

Portmoak is in the Kinross/Fife area, not the Scottish Borders. A regional site
such as Aboyne, Milfield or Portmoak must not be confused with the summit used
as its model sampling anchor. Forecast terrain elevation comes from the forecast
response and is stored separately; ridge crest elevations are never substituted
for grid terrain elevation.

## Original Duo Discus polar

Source inspected: [XCSoar PolarStore.cpp, original Duo Discus entry](https://github.com/XCSoar/XCSoar/blob/d15883efacf62cb5ff25792182a0c295c2b42299/src/Polar/PolarStore.cpp#L130).
The [field definitions](https://github.com/XCSoar/XCSoar/blob/d15883efacf62cb5ff25792182a0c295c2b42299/src/Polar/PolarStore.hpp)
specify speed in km/h, signed sink in m/s, reference mass in kg and area in m².
XCSoar publishes its implementation under GPL-2.0-or-later; the numerical
reference points are attributed here. The interpolation/scaling implementation
in this repository was written independently.

The selected entry is **Duo Discus**, not Duo Discus T or xT:

| Speed km/h | Positive-down sink m/s |
| ---: | ---: |
| 103 | 0.64 |
| 152 | 1.25 |
| 200 | 2.51 |

Reference mass 615 kg and area 16.4 m² give reference loading 37.5 kg/m².
An independent quadratic interpolation in SI units yields
`s(v) = 0.0018439301493793376 v² - 0.08579539238375739 v + 1.5852682516305459`.
For target loading W, use `k = sqrt(W / 37.5)` and
`s_W(v) = k * s(v / k)`. Thus the coefficients are:

| Configuration | Loading kg/m² | a | b | c |
| --- | ---: | ---: | ---: | ---: |
| Dry | 34 | 0.0019365139983860099 | -0.08579539238375739 | 1.5094773012081066 |
| Ballasted | 43 | 0.0017219711517593354 | -0.08579539238375739 | 1.697545235324577 |

Both fitted curves have maximum L/D about 44.77. The three reference speeds
define the interpolation interval after scaling. They are not stall, manoeuvre,
rough-air or VNE limits; this module deliberately refuses extrapolation. Aircraft
mass, permitted loading, instrument calibration, surface condition, density and
actual G-CKML performance require separate validation before operational use.
MacCready and task-speed computations are reserved for Module 3.

## Open-Meteo

Documentation: [forecast API](https://open-meteo.com/en/docs),
[UK Met Office](https://open-meteo.com/en/docs/ukmo-api),
[DWD](https://open-meteo.com/en/docs/dwd-api), and
[ECMWF](https://open-meteo.com/en/docs/ecmwf-api).
The documentation/API hosts were blocked by the environment's proxy during
implementation, so the provider's published source was inspected through the
available GitHub connection at commit
[`ae5caa86c8b8b5e9c6d5f0b8b48f69dc36487bb4`](https://github.com/open-meteo/open-meteo/tree/ae5caa86c8b8b5e9c6d5f0b8b48f69dc36487bb4).
Source inspection establishes the intended contract, not deployment equivalence
or a successful live service check.

- `Sources/App/Controllers/ForecastapiController.swift` exposes
  `ukmo_uk_deterministic_2km`, `icon_eu` and **`ecmwf_ifs025`** at `/v1/forecast`.
  `ifs025` remains the local configuration key, not the outbound API name.
- `Sources/App/Helper/ForecastapiQuery.swift` defines `forecast_hours` starting at
  the current hour. Requests use 49, 121 and 145 samples to include the 48-,
  120- and 144-hour endpoints respectively. Model data can stop earlier or be null.
- `ForecastapiController.swift` represents unsupported hourly fields with null
  arrays and undefined units. All 61 requested variables are sent; the normalizer
  preserves missing arrays, counts and units without substituting another model.
- `Sources/App/UKMO/UkmoVariable.swift` excludes pressure-level vertical velocity
  from the UK 2 km download. The generic pressure variable exists in the API but
  is not proof it is present for that model.
- `Sources/App/Icon/IconVariable.swift` does not define pressure vertical velocity
  for ICON-EU. Main forecast runs extend to 120 h; intermediate runs are shorter.
- `Sources/App/Ecmwf/EcmwfVariable.swift` does not have a native 950 hPa level.
  It declares vertical velocity in **m/s**, not Pa/s. Returned units are always
  retained so Module 3 cannot inadvertently convert an already converted value.
- Boundary layer height, lifted index, soil-depth aliases and precipitation
  probability depend on the model and provider derivation/ensemble support.
  Their availability must be tested from each response, never presumed.
- Hourly sampling does not imply hourly native model resolution. No run timestamp
  is inferred from retrieval time. Surface and pressure winds use the meteorological
  direction **from** which wind blows; vectors must use that convention.

The specification later asks for shear at 900 hPa, but only requests 925 and
850 hPa around it in Module 2. Module 3 must either explicitly request 900 hPa
when supported or document interpolation of wind **components**, not linear
interpolation of compass angles. No such interpolation is performed here.

The free endpoint has provider-defined usage limits and terms. Geographic grid
generation is separate from fetching; large grids are opt-in. No model archive,
ensemble confidence, numerical wave prediction or convergence analysis is
implicitly supplied by this ingestion layer.

## Aviation Weather Center

Public endpoints: [METAR JSON](https://aviationweather.gov/api/data/metar) and
[TAF JSON](https://aviationweather.gov/api/data/taf); [API documentation](https://aviationweather.gov/data/api/).
The implementation targets decoded AWC records (`icaoId`, `obsTime`, `wdir`,
`wspd`, `wgst`, `visib`, `altim`, `clouds`, and TAF `fcsts`).

- Wind speeds and gusts are knots; directions are true degrees FROM, or `VRB`.
- `visib` is statute miles in this JSON contract, even for UK stations; `6+`,
  `P6SM`, fractions and censored values are preserved when converting to metres.
- `altim` is hPa. Values resembling inHg are rejected, never guessed/conflated.
- Cloud `base` and `vertVis` are feet AGL; station elevation is metres.
- TAF `issueTime`, validity and period timestamps are retained independently.
  Conditional groups overlap and must not be treated as simultaneous certain
  observations. Raw records retain fields beyond the normalized subset.
- Some requested ICAO stations do not routinely publish METARs or TAFs, and
  station identifiers/operating status may change. Missing reports are explicit;
  they are never filled with nearby or synthetic reports.

Live checks subsequently became possible after the network-domain draft was
saved. See [validation results](validation.md) for outcomes. A live TAF check
revealed empty-string visibility in conditional groups at EGOS, EGTC and EGXW;
this is now retained as unspecified visibility and covered by a regression test.
Offline tests are synthetic and exercise successful and failing response
contracts, data quality, transport handling and database concurrency.
