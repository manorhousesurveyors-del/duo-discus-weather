"""Offline parser/provenance and transactional updater regression checks."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

import bga_turnpoints as b

CUP = ('name,code,country,lat,lon,elev,style,rwdir,rwlen,freq,desc\r\n'
       '"MYN-Mynd",MYN,UK,5231.119N,00252.857W,1397ft,4,180,1000m,123.815,"Clubhouse, hangar\nsecond line"\r\n'
       '-----Related Tasks-----\r\n"Task","Mynd","Mynd","Mynd"\r\n')
URL='https://soaringweb.org/TP/BGA/bga2026a.cup'


class BgaTests(unittest.TestCase):
    def test_cup_units_optional_fields_and_multiline(self):
        p,=b.parse_turnpoints(CUP,official=True)
        self.assertEqual((p.code,p.name),('MYN','Mynd'))
        self.assertAlmostEqual(p.latitude,52.51865)
        self.assertAlmostEqual(p.longitude,-2.88095)
        self.assertAlmostEqual(p.elevation_m,425.8056)
        self.assertEqual(p.runway_direction,180)
        self.assertEqual(p.runway_length_m,1000)
        self.assertEqual(p.frequency,'123.815')
        self.assertEqual(p.description,'Clubhouse, hangar\nsecond line')

    def test_coordinate_formats_and_invalid_minutes(self):
        for text,lat,expected in [('52.5',True,52.5),('52 30 0 N',True,52.5),
                                   ('002:30:00W',False,-2.5),('-2.5',False,-2.5),('5230.000S',True,-52.5)]:
            self.assertAlmostEqual(b.parse_coordinate(text,latitude=lat),expected)
        for text in ['5260.000N','NaN','91N','-52N','52W','',None,True]:
            with self.subTest(text=text),self.assertRaises(ValueError): b.parse_coordinate(text,latitude=True)

    def test_csv_and_json_and_geojson(self):
        csv='trigraph;full name;latitude;longitude;elevation_ft\nABC;A café;52.5;-2.5;1000\n'
        p,=b.parse_turnpoints(csv.encode('cp1252'),'csv')
        self.assertAlmostEqual(p.elevation_m,304.8)
        self.assertEqual(p.name,'A café')
        records=[dict(code='ABC',name='Test',latitude=52.5,longitude=-2.5,elevation_m=120)]
        for body in [records,{'waypoints':records},{'turnpoints':records}]:
            self.assertEqual(b.parse_turnpoints(json.dumps(body))[0].elevation_m,120)
        body={'type':'FeatureCollection','features':[{'type':'Feature','properties':{'code':'ABC','name':'Test'},
              'geometry':{'type':'Point','coordinates':[-2.5,52.5,120]}}]}
        self.assertEqual(b.parse_turnpoints(json.dumps(body))[0].latitude,52.5)

    def test_malformed_input_fails_whole_import(self):
        for body in ['[]','{"type":"FeatureCollection","features":[null]}',
                     '{"type":"FeatureCollection","features":[{"geometry":{"type":"Point","coordinates":null}}]}',
                     '[{"latitude":NaN}]',CUP.split('-----')[0]+CUP.splitlines()[1]+'\n',
                     'name,code,lat,lon\nX,ABC,52,-2,unexpected\n']:
            with self.subTest(body=body[:40]),self.assertRaises(ValueError): b.parse_turnpoints(body)
        with self.assertRaises(ValueError): b.parse_turnpoints('x'*(b.MAX_BYTES+1))

    def test_bundled_catalog_is_complete_attributed_and_checksummed(self):
        catalog=b.load_catalog(); b.validate_uk_catalog(catalog.turnpoints)
        self.assertGreaterEqual(len(catalog.turnpoints),500)
        self.assertIn('mynd',catalog.by_code['MYN'].name.lower())
        self.assertAlmostEqual(catalog.by_code['MYN'].latitude,52.51865,delta=.05)
        self.assertTrue(all(p.official and len(p.code)==3 for p in catalog.turnpoints))
        self.assertIn('unofficial mirror',catalog.attribution)
        self.assertEqual(len(catalog.source_sha256),64)
        self.assertGreaterEqual(catalog.published_version,'2026a')
        self.assertEqual(b.search_turnpoints(catalog.turnpoints,'Long Mynd')[0].code,'MYN')

    def test_sync_atomic_failure_unchanged_and_changed(self):
        original=b.load_catalog()
        raw=json.dumps([asdict(p) for p in original.turnpoints]).encode()
        url=URL.replace('.cup','.json')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'catalog.json';b.save_catalog(original,path);before=path.read_bytes()
            result=b.update_catalog(path,urls=[url],fetch=lambda _:raw)
            self.assertFalse(result.changed);self.assertEqual(path.read_bytes(),before)
            for bad in [b'not a catalog',b'[]',CUP.encode()]:
                with self.assertRaises(ValueError): b.update_catalog(path,urls=[url],fetch=lambda _:bad)
                self.assertEqual(path.read_bytes(),before)
            changed=[replace(p,elevation_m=p.elevation_m+1) if p.code=='MYN' else p for p in original.turnpoints]
            result=b.update_catalog(path,urls=[url],fetch=lambda _:json.dumps([asdict(p) for p in changed]).encode())
            self.assertTrue(result.changed);self.assertEqual(result.modified,1)
            self.assertEqual(b.load_catalog(path).by_code['MYN'].elevation_m,original.by_code['MYN'].elevation_m+1)
            with self.assertRaises(ValueError): b.update_catalog(path,urls=[url.replace('2026a','2025a')],fetch=lambda _:raw)

    def test_discovery_finds_new_version_and_rejects_foreign_hosts(self):
        page=b'<a href="BGA%202027%20Revision%20a%20SeeYou.cup">CUP</a><a href="bga2026a.cup">old</a><a href="https://evil.test/x.cup">x</a>'
        urls=b.discover_sources(lambda _:page)
        self.assertIn('2027',urls[0]);self.assertFalse(any('evil.test' in u for u in urls))
        for url in ['http://soaringweb.org/file.cup','https://evil.test/x','https://user:pass@soaringweb.org/x']:
            with self.assertRaises(ValueError):b._source_url(url)

    def test_checksum_detects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'data.json';b.save_catalog(b.load_catalog(),path)
            data=json.loads(path.read_text());data['turnpoints'][0]['latitude']+=.01;path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'checksum'):b.load_catalog(path)
