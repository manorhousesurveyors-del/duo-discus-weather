# Soaring calculation methods

Calculations use SI internally, meteorological wind **from** directions,
UTC instants, and Europe/London dates for task windows and alert deduplication.
Displayed cloud/mixing heights are feet AMSL; mixing depth is metres AGL.
Input profiles remain specific to one model. Unsupported exact hours produce
missing results, not a nearest-hour or extrapolated forecast.

## Thermals, cloud and streets

Potential condensation height follows the requested Hennig spread approximation:

`LCL_ft_AMSL = 400 × max(T_2m − Td_2m, 0) + terrain_m / 0.3048`.

Model boundary-layer height is used as AGL depth if available. Otherwise a dry
surface parcel's potential temperature is compared with the pressure profile.
The first crossing of environmental potential temperature above the parcel
brackets mixing height; interpolation is linear in height/theta. No thermal
excess is added. The initial surface comparison has a 0.01 K numerical tolerance.
An unbracketed top remains unavailable. Pressure levels below model terrain
are excluded. This estimate cannot resolve shallow inversions between levels.

Usable thermal top is the smaller of the LCL and mixing top. If the mixing
top is more than 200 ft below the LCL, the result is labelled potential blue
thermals instead of implying cumulus will form. Threshold flags retain net
climb ≥3.5/4.5 kt and potential cloud base ≥4,500/5,000 ft. Alert eligibility
uses **usable** top ≥4,500 ft, which also requires LCL ≥4,500 ft.

Sensible heat is an explicit proxy, not a flux measured by these APIs:

```
available_energy = max(direct_radiation, 0) × 0.8 × 0.7
wetness = clamp((soil_moisture − 0.05) / 0.35, 0, 1)
Bowen_ratio = 4 × (1 − wetness) + 0.25 × wetness
H = available_energy × Bowen_ratio / (1 + Bowen_ratio)
rho = surface_pressure_Pa / (287.05 × temperature_K)
w_star = [g / temperature_K × H / (rho × 1004) × mixing_depth_m]^(1/3)
net_climb_knots = w_star × 3600/1852 − 1.6
```

The fixed albedo/energy factors and moisture-to-Bowen mapping have **not** been
calibrated against UK glider observations. Direct radiation excludes diffuse
input. `w*` is a convective velocity scale, not a measured thermal-core updraft.
Missing soil moisture uses Bowen 1 nominal and 0.25 conservative; alerts and
task capacity use the conservative climb. Missing radiation/depth/pressure/
temperature does not produce a climb estimate. Zero radiation produces −1.6 kt
after the requested circling sink deduction.

Spread-out risk requires RH >85% at the available level nearest mixing top and
low cloud >60%. Cloud-street potential requires mean available 925/850 hPa wind
within the boundary layer of 15–25 kt and surface-to-inversion directional
shear <20°. These are threshold flags, not a cloud-resolving simulation.

## Wave potential

Wind vectors use `u = −speed sin(direction)`, `v = −speed cos(direction)`.
Intermediate pressure levels use log-pressure interpolation of vectors and
scalars without extrapolation. Interpolating vectors avoids the 359°/1° error.
Potential temperature is `theta = T_K (1000/p_hPa)^(Rd/Cp)`.

| Evidence | Points |
| --- | ---: |
| Strongest 925/850 hPa wind ≥18 kt and within 30° of a ridge normal | 30 |
| 900–500 hPa directional shear <30° | 20 |
| 850–700 hPa `N² = g/theta × dtheta/dz` ≥1e−5 s⁻² | 20 |
| Positive lower Scorer parameter decreasing between 850 and 700 hPa | 20 |
| Available 850/700 hPa model vertical motion, with ascent at either level | 10 |

Scorer parameter includes both terms, `l² = N²/U_perp² − U_perp''/U_perp`.
Nonuniform-height finite differences use at least four pressure levels.
Cross-ridge speeds must exceed 3 m/s throughout the sampled column; weak or
reversing flow fails the Scorer criterion. Any failed core flow/shear/stability/
Scorer condition caps the score at 70. Missing evidence is provisional and
cannot trigger; complete scores ≥75 can trigger a wave-potential alert.

Vertical velocity in m/s is preserved. Pressure velocity uses
`w = −omega/(rho × g)` with level-specific density; hPa/s is converted to Pa/s
first. Positive w means upward. A model-supplied zero/descent is distinct from
missing motion and can leave a complete score of 90 when all core tests pass.

The regional score cannot resolve rotor, crests or safe entry/exit. Its summit
anchors are approximate and scores are not probabilities. Wave Watch uses the
separate terrain calculation below; it does not add assumed lift to XC tasks.

## Terrain-forced Wave Watch

Mapzen Terrarium elevations form one 504 × 324 grid covering 50–59°N, −8–2°E.
Nominal spacing is 1,997 m at 54.5°N in Web Mercator; ground scale varies with
latitude. This includes eastern Ireland, not the whole island.
After light spatial smoothing, terrain slope forces vertical velocity through
`w0 = U dh/dx + V dh/dy`. Reported U⊥ is the RMS slope-normal wind component;
the Fourier forcing itself uses the local two-dimensional gradient.

Potential temperature gradients establish `N² = g/theta dtheta/dz`. The along-flow
Scorer profile includes curvature, `l² = N²/U² − U''/U`. A lower positive Scorer
value yields `lambda = 2π/sqrt(l²)`; decreasing values aloft indicate possible
trapping. Diagnosed wavelengths are not clamped into the suggested 5–18 km range.

A windowed, reflect-padded Fourier transform retains horizontal wavelengths of
2–40 km. A damped causal harmonic response favours the diagnosed wavelength;
its assumed spatial decay has an e-folding length of four wavelengths (successive
bounce factors approximately 0.779, 0.607, 0.472, 0.368). A WKB transfer integrates
vertical phase and evanescent attenuation using the forecast profile. Nonpositive
stability, reversing or very weak flow blocks upward propagation. No extrapolation
beyond the profile, below-ground wave values or invented missing terrain is used.

This approximates both signed phases; damping, linearity and horizontal uniformity
of each atmospheric column are assumptions. It is not a terrain-resolving NWP
solver and has not been calibrated against observed UK wave climbs. The displayed
2 km detail and interpolated sections must not imply equivalent forecast skill.

Up to eight spatially separated regional/ridge atmosphere profiles each force
the entire terrain grid. Gaussian weights (110 km scale) blend their signed
solutions smoothly; there are no nearest-site ownership or crop boundaries.
Cells farther than 350 km from a supported profile remain unknown. Available
pressure-level model vertical motion adds a background ascent/sink component;
missing motion is not fabricated. Below-terrain and unsupported altitude cells
remain masked. This blend of horizontally uniform linear solutions is an
approximation, not a spatially varying atmospheric PDE or SkySight’s algorithm.
The finite domain is padded and tapered at its outer edges. The older sixteen
500 m grids remain only for reference/regression coverage.

## Independent UK RASP-style day stars

Distance bands begin at 0 / 50 / 180 / 350 / 500 km for 1–5 stars. The same
conservative local PFD calculation used by the dashboard supplies distance.
All eight 10:00–18:00 local intervals require climb, usable height, rain and
spread-out evidence, otherwise the rating is unavailable.

Quality caps use median net climb, median usable height AGL and the longest
continuous soarable window. Three/four/five stars require respectively at least
1.5/2.5/3.5 kt, 1,800/3,000/4,500 ft AGL and 3/4.5/6 hours. Rain ≥1 mm/h or
usable depth <500 m excludes an interval. Spread-out in at least 25% of usable
hours caps at three stars; 50% caps at two. Wave potential is labelled separately
and does not upgrade the thermal XC rating.

These are explicit screening thresholds chosen for the requested UK distance
categories, not the official RASP algorithm or observed-flight calibration.
The selected origin's rating appears on the dashboard; alerts show rated regions
and use high priority when any fully supported region reaches three stars.

## MacCready route capacity

For positive-down quadratic polar `s(v)=a v²+b v+c`, optimal still-air cruise
speed solves `v s'(v) − s(v) = MC`, giving `v=sqrt((MC+c)/a)`. It is bounded to
the attributed polar fit interval, which is not an aircraft speed limit.

For positive net climb c, a climb/glide cycle has equivalent horizontal airspeed
`q=v c/(c+s(v))`. Along a leg, cycle-average ground speed is
`sqrt(q² − crosswind²) + tailwind`. This accounts for drift during climb and
glide. If crosswind cannot be cancelled, or net ground progress/climb is
nonpositive, sustainable speed is zero. Wind-specific reoptimization of
MacCready speed, water dumping and final glide are not modeled.

Use the lesser conservative endpoint climb and average endpoint 925 hPa wind
(850 hPa fallback). Model choices are recorded for each leg/hour. Nine samples
from 10:00 through 18:00 describe **eight** travel intervals. A flight can start
at any hourly boundary, move sequentially across legs, and must finish by 18:00.
A missing or non-soarable interval ends that candidate flight; disjoint good
hours are never added together.

The 250/300/500/750 km variants scale leg lengths proportionally. Actual route
completion is reported separately. Endpoint sampling omits mid-leg weather;
launch/landing reserves, usable-height requirements, airspace, terrain, cloud
avoidance, turnpoints and operational limits require separate planning.

## Convergence

A 0.5° grid covers 50–55.5° N and −6.5–2° E (216 points, about 56 km north/south
spacing). On spherical coordinates, divergence is
`[du/dlambda + d(v cos(phi))/dphi] / [R cos(phi)]`; convergence is its negative.
The dewpoint gradient uses physical x/y distances and is displayed K/100 km.
Missing values propagate through the finite-difference stencil. Edge cells
cannot trigger because their derivatives are one-sided.

Candidates require surface convergence ≥2e−5 s⁻¹ and dewpoint gradient
≥1 K/100 km. Upper convergence ≥1e−5 s⁻¹ at 950 hPa marks a deep/synoptic
candidate. Missing 950 hPa winds can be interpolated between above-terrain
1000/925 hPa winds. Coastal cells with neighboring land and sea plus radiation
>100 W/m² are sea-breeze candidates if the deep condition is absent. Remaining
cells are surface candidates. These labels screen possible causes; they do not
establish front dynamics. Narrow coastal fronts may be entirely unresolved.

Strong alerts require ≥80% valid surface grid coverage and three adjacent cells
with convergence ≥3e−5 s⁻¹ within one Welsh Marches, Pennines or Southern England
region. Each grid is evaluated within one model. The watchdog prefers ICON with
sufficient coverage, otherwise ECMWF; the dashboard honors the selected model.
The land mask assists classification/interpolation, not terrain elevation or
coast-resolving atmospheric physics.

## Calibration and provenance

Thresholds implement the specification plus the screening choices above.
Synthetic fixtures verify units, signs, formulas, gaps and delivery decisions;
they do not establish forecast skill. Live API checks establish contract
compatibility, not meteorological accuracy. Use attributed historical profiles
and observed flights to calibrate estimates before relying on numeric thresholds.
The Gold-Standard storage API supports observations without silently replacing
live model data. See [data sources](data-sources.md) for polar/ridge provenance.


## Cached terrain preparation

The continuous DEM and read-only smoothed terrain, x/y slopes, padded FFT and
wavenumber grids are prepared once per server lifetime. Atmospheric solutions
reuse those NumPy arrays; no wind or stability result is treated as static.
Wave products are cached for 30 minutes by forecast retrieval identity and valid
hour. The complete Wave Watch document also caches serialization/compression,
with catalog and airspace versions in its key. A new forecast/hour recomputes the
field; unsupported profiles stay unavailable. Display-only rounding never changes
the terrain solution or task export coordinates.
