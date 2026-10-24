# BGA turnpoint catalog

`bga_turnpoints.json` is normalized from the BGA **2026a** SeeYou CUP file:

- [Source index and BGA provenance](https://soaringweb.org/TP/BGA/HomePage.html)
- [Current downloadable formats](https://soaringweb.org/TP/BGA/files.html)
- [2026a original CUP](https://soaringweb.org/TP/BGA/BGA%202026%20Revision%20a%20SeeYou.cup)
- [Publisher's terms / disclaimer](https://soaringweb.org/TP/disclaimer.html)
- [Definitive list index](https://www.newportpeace.co.uk/turningpoints.htm)

Courtesy of **Tim Newport-Peace**, mirrored by **John Leibacher / Worldwide
Soaring Turnpoint Exchange**. The mirror calls these “Unofficial BGA Control
Points”, for flight verification and not navigation, with no guarantee of
completeness or accuracy. The BGA prepares its table in good faith and takes
no responsibility for its accuracy or use in airborne navigation systems.
Consult the definitive list, stop-press updates and current airspace information.
The repository does not relicense the underlying waypoint data or claim BGA
endorsement. Original source fields and descriptions are preserved in `extras`.

The 1,390-record release covers the publisher's England, Wales and Scotland list.
It is not a claim of verified Northern Ireland coverage. `official: true` denotes
a published BGA trigraph imported from the validated whole-country source;
it does not make this third-party mirror an official navigation publication.

The catalog records the original download hash, canonical-content hash, version,
retrieval time and URL. Quarterly checks discover the current download filename;
see [task planning](../docs/task-planning.md) for validation and update behavior.

## UK airfields

`uk_airfields.json` was retrieved on **24 October 2026** from:

- [OurAirports airport metadata via datasets/airport-codes](https://github.com/datasets/airport-codes)
- [OurAirports runway data](https://github.com/davidmegginson/ourairports-data)
- [NOAA station capabilities](https://aviationweather.gov/data/cache/stations.cache.xml.gz)

OurAirports airport/runway data is public domain. The JSON retains source URLs,
retrieval time, runway metadata, reporting capabilities and club aliases.
It includes 1,098 airfields in GB, the Isle of Man and Channel Islands,
including 130 unique METAR/TAF station codes from NOAA's capability catalog.
Heliports and most closed facilities are excluded. Kirton in Lindsey is retained
because it was requested, with its source status explicitly marked **closed**.
Reporting capability is not a guarantee of a current report. No missing runway,
elevation or ICAO code is fabricated. Club names are aliases, not BGA trigraphs.

## UK airspace boundaries

Two attributed GeoJSON snapshots are bundled:

| File | AIRAC period | Boundary volumes | Source |
| --- | --- | ---: | --- |
| `uk_airspace.geojson` | 1–28 October 2026 | 801 | [Publisher commit 98e023e](https://github.com/ahsparrow/airspace/tree/98e023e9b42dfaa9efcfd90d31109fa833d91e32) |
| `uk_airspace_next.geojson` | 29 October–25 November 2026 | 799 | [ASSelect YAIXM release](https://asselect.uk/yaixm.json), commit `6b7f9d95535a894ce8bff4972da00fb00eec8ead` |

Data is by **Alan Sparrow / ASSelect**, based on the UK AIP and work by George
Knight, Geoff Brown, Peter Desmond and Rory O'Connor. The source author states:
“To the extent possible under law, Alan Sparrow has waived all copyright and
related or neighbouring rights to this file.” Each output retains attribution,
source URL/commit, SHA-256 download hashes, retrieval time and AIRAC dates.

The current set was taken from the publisher's preceding release because
ASSelect already announces 29 October. Its release notes confirm 1 October.
The next set uses ASSelect's machine-readable `airac_date`; its release-notes
heading contains a 2027 typo, so that heading is not used as the effective date.
The planner automatically chooses the latest set effective on the selected day.
Outside the recorded AIRAC period it displays an explicit refresh warning.

Controlled zones/areas, ATZ/MATZ, danger/prohibited/restricted areas, RMZ/TMZ,
parachuting, HIRTA, gas-vent and laser hazard volumes are retained. Gliding-site
symbols, unlicensed/microlight sites, ILS symbols and gliding access concessions
are excluded. LOAs never remove restrictions automatically. This is a third-party
transcription principally at/below FL195, not a complete official AIP or NOTAM feed.
Temporary restrictions retain their published names/rules; current activation
must be checked separately. Source coverage/vertical limits remain material.

[scripts/update_airspace.py](../scripts/update_airspace.py) converts YAIXM
lines/circles/arcs into GeoJSON. Arc steps are at most 3°. Five intersecting rings
in each snapshot are split into valid multipolygons using Shapely's linework
method, retaining every boundary edge and every small polygon; none are dropped.
This normalization is recorded per feature. All output polygons pass topology
validation. No independent survey of the underlying AIP transcription is claimed.

Refresh the announced ASSelect release manually:

```bash
.venv/bin/python scripts/update_airspace.py
```

This saves a current or upcoming file according to its effective date; it does
not publish or commit automatically. Reproduce the present current snapshot:

```bash
.venv/bin/python scripts/update_airspace.py --commit 98e023e9b42dfaa9efcfd90d31109fa833d91e32 --effective-date 2026-10-01
```

Explicit OpenAir/GeoJSON uploads override bundled data and remain under ignored
`var/`. Uploads without AIRAC dates are labelled for verification. Nearby airport
radii are advisory proximity checks, never substituted for airspace boundaries.


## Continuous wave terrain

`uk_continuous_terrain.npz` is the Wave Watch production grid: **504 × 324**
real elevation samples spanning **50–59°N, −8–2°E**, including Great Britain
and eastern Ireland. Nominal spacing is **1,996.6 m at 54.5°N**. Web Mercator
scale varies with latitude. Its SHA-256 is
`a69551925b53fb91aec055d4a3e30ced3373670f8b78d452c9f1c528fc12834e`.

The adjacent JSON records verified HTTPS retrieval, bounds, projection and
individual zoom-8 input tile hashes. Source and attribution are Mapzen/AWS
Terrain Tiles, as detailed below. A joined Terrarium tile mosaic is resampled
bilinearly across tile seams; negative heights become sea level. The loader
checks the archive hash, dimensions and finite numerical values before use.
Reproduce it with `scripts/update_continuous_terrain.py`. Numerical samples and
browser checks cover Wales, England and the Scottish Highlands. These elevations
are neither surveyed obstacle data nor evidence of atmospheric forecast skill.

## Retained wave terrain reference grids

`uk_wave_terrain.npz` contains sixteen 256 × 256 elevation grids at 500 m
spacing. `uk_wave_terrain.json` records each grid's bounds, centre, shape, tile
provenance, verified HTTPS retrieval time and SHA-256 hashes. The archive hash is
`7c8b350c2008a3348a741387f7e1969ebf13d7c35a8918455f3843e50de538e8`.

Source: [Mapzen Terrain Tiles on AWS](https://registry.opendata.aws/terrain-tiles/),
34 zoom-8 Terrarium PNG tiles from `elevation-tiles-prod/terrarium`. Attribution:
Mapzen / AWS Terrain Tiles and the underlying SRTM, GMTED and other attributed
sources; see [Mapzen source attribution](https://github.com/tilezen/joerd/blob/master/docs/attribution.md).
The Terrarium formula is `R*256 + G + B/256 − 32768` metres. Bilinear resampling
forms the grids; negative sea/bathymetric heights are set to zero for this
land-based diagnostic. No hand-drawn or synthetic hill shapes are substituted.

[scripts/update_wave_terrain.py](../scripts/update_wave_terrain.py) reproduces the
archive and records hashes of every input tile. The loader verifies the archive
hash and numerical bounds before use. 500 m terrain samples are not obstacle
clearance data, and do not confer 500 m atmospheric forecast accuracy.

OGN traffic is fetched live from [OGN / SpotTheGlider](https://live.glidernet.org/)
and the [OGN device registry](https://ddb.glidernet.org/download/?j=1). Reports
are not bundled in the repository. Registration visibility follows the registry's
tracking/identification flags; missing or mismatched identities remain anonymous.
