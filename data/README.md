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

Current airspace geometry is not bundled. Users can supply a validated OpenAir
or polygon GeoJSON file through Task planner; it is saved under ignored `var/`.
Absent geometry is reported as unassessed. Do not treat airport proximity circles
as controlled-airspace boundaries.
