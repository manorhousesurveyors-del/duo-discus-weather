"""Headless actual canvas regression, using synthetic wind and OGN fixtures.

No live provider dependency. Optional --live-traffic verifies an independently
captured receiver JSON too, without changing its observation timestamps.
"""
import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.async_api import async_playwright
from app import build_map
from bga_turnpoints import load_catalog
from map_weather import national_grid, wind_payload
from tests.fixtures import forecast

PROBES=((50.5,-3.5),(52.5,-2.9),(54.5,-2.),(57.2,-3.8),(53.3,-7.5))


def documents(live_wind=None):
    now=datetime(2026,11,8,12,tzinfo=UTC)
    # Deliberately omit north/west chunks: the Python payload must repair holes.
    forecasts=tuple(forecast(p,start=now.replace(hour=0),hours=120) for p in national_grid()
                    if p.latitude<56 and p.longitude>-7)
    points=load_catalog().turnpoints
    wind,_=build_map((),now,layers=[],turnpoints=points,visual_mode='wind',wind_product=json.loads(Path(live_wind).read_text()) if live_wind else wind_payload(forecasts,now),live_airfields=False)
    traffic,_=build_map((),now,layers=[],turnpoints=points,visual_mode='clean',map_identity='traffic',include_modes=False,
                        show_airfields=True,live_airfields=True)
    return {name:(view.get_root().render(),view.get_name()) for name,view in [('wind',wind),('traffic',traffic)]}


async def main(args):
    docs=documents(args.live_wind);out=Path('var/wind-ogn-browser');out.mkdir(parents=True,exist_ok=True)
    errors=[];cache={};results={}
    now=datetime.now(UTC)
    rows=[{'id':'fixture'+str(i),'lat':lat,'lon':lon,'registration':'FIXTURE '+str(i),'competition_id':cn,'badge':cn,'category':'glider' if i%2==0 else 'other',
           'aircraft_type':1,'aircraft_model':'Synthetic glider','altitude_ft':4000,'groundspeed_kt':55 if i else 0,
           'airborne':bool(i),'heading':i*60,'vario_kt':i-2.,'observed_at':(now-timedelta(seconds=20+i*80)).isoformat(),
           'highlight':False,'source':'Synthetic verification fixture'}
          for i,((lat,lon),cn) in enumerate(zip(PROBES,('TEST1','TEST2','TEST3','TEST4','?88')))]
    body={'schema':2,'status':'live','source':'Isolated protocol test fixture','fetched_at':now.isoformat(),'aircraft':rows}
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1450,'height':1100})
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.add_init_script('window.Streamlit={setComponentValue:()=>{}};')
        async def asset(route):
            url=route.request.url
            if '/tile/' in url or '/tiles/' in url or 'tile.openstreetmap' in url:
                await route.abort();return
            def fetch():
                if url not in cache:
                    with urllib.request.urlopen(url,timeout=15) as r:cache[url]=(r.headers.get_content_type(),r.read())
                return cache[url]
            try:
                mime,raw=await asyncio.to_thread(fetch);await route.fulfill(content_type=mime,body=raw)
            except Exception:await route.abort()
        await page.route('https://**/*',asset)
        traffic_reads=0
        async def local(route):
            nonlocal traffic_reads
            url=route.request.url
            if '/app/static/ogn/uk.json' in url:
                traffic_reads+=1
                reply=body if traffic_reads>1 else {**body,'aircraft':[],'status':'connecting'}
                await route.fulfill(content_type='application/json',body=json.dumps(reply));return
            name=url.rsplit('/',1)[-1].replace('.html','')
            await route.fulfill(content_type='text/html',body=docs[name][0])
        await page.route('http://duo.test/**',local)
        async def open_map(name):
            await page.goto('http://duo.test/'+name+'.html',wait_until='domcontentloaded')
            await page.wait_for_function(f'window.{docs[name][1]} && window.{docs[name][1]}._loaded')
            await page.evaluate(f'()=>{{window.map_div=window.{docs[name][1]};window.events=[];window.duoMapEvent=(...args)=>window.events.push(args);}}')
        await open_map('wind')
        assert not errors,errors
        coverage=await page.evaluate('''probes=>{
          const m=window.map_div,w=m._duoWind;m.fitBounds([[49.5,-11],[61,2.5]],{animate:false});
          const bad=[];
          for(const [lat,lon] of probes)for(let t=0;t<120;t++)for(let l=0;l<6;l++){
            const v=w.sampleAt(lat,lon,t,l);if(!v||!Number.isFinite(v.u)||!Number.isFinite(v.v))bad.push([lat,lon,t,l]);
          }
          return {bad,bounds:w.bounds,coverage:w.coverage};
        }''',PROBES)
        assert not coverage['bad'],coverage
        await page.wait_for_timeout(250)
        swarm=await page.evaluate('''()=>{const m=map_div,w=m._duoWind,quadrants=[0,0,0,0];
          for(const p of w.swarm){const ll=m.containerPointToLatLng([p.x,p.y]);if(w.sampleAt(ll.lat,ll.lng))quadrants[(ll.lat>55.25?2:0)+(ll.lng> -4.25?1:0)]++;}
          return {quadrants,validRaster:w.raster.filter(Boolean).length};}''')
        assert all(n>10 for n in swarm['quadrants']),swarm
        assert swarm['validRaster']>100,swarm
        await page.locator('[data-reference="bga"]').check()
        assert await page.evaluate('map_div.hasLayer(map_div._duoBga)')
        await page.locator('[data-reference="bga"]').uncheck()
        await page.screenshot(path=str(out/'wind.png'))
        results['wind']={'probe_hours_levels':5*120*6,'swarm':swarm,'coverage':coverage['coverage']}
        await open_map('traffic')
        await page.wait_for_function('map_div._duoTraffic.count===5')
        assert not await page.evaluate('map_div.hasLayer(map_div._duoBga)||map_div.hasLayer(map_div._duoAirfields)')
        for control,key in [('bga','_duoBga'),('airfields','_duoAirfields')]:
            await page.locator('[data-reference="'+control+'"]').check()
            assert await page.evaluate(f'map_div.hasLayer(map_div.{key})')
            await page.locator('[data-reference="'+control+'"]').uncheck()
            assert not await page.evaluate(f'map_div.hasLayer(map_div.{key})')
        await page.locator('[data-ogn-filter="ground"]').uncheck()
        assert await page.evaluate('map_div._duoTraffic.count')==4
        await page.locator('[data-ogn-filter="ground"]').check()
        await page.locator('[data-ogn-filter="other"]').uncheck()
        assert await page.evaluate('map_div._duoTraffic.count')==3
        await page.locator('[data-ogn-filter="glider"]').uncheck()
        assert await page.evaluate('map_div._duoTraffic.count')==0
        await page.locator('[data-ogn-filter="other"]').check()
        assert await page.evaluate('map_div._duoTraffic.count')==2
        await page.locator('[data-ogn-filter="glider"]').check()
        colours=await page.evaluate('[...new Set(map_div._duoTraffic.layer.records.map(p=>p.colour))].sort()')
        assert colours==['#38BDF8','#FDE047'],colours
        assert await page.locator('[data-ogn="demo"]').count()==0
        assert 'Isolated protocol test fixture' in await page.locator('.duo-ogn-state').inner_text()
        await page.evaluate('map_div.setView([52.5,-2.9],7,{animate:false})')
        await page.wait_for_timeout(100)
        assert await page.evaluate('window.events.length')==0,await page.evaluate('window.events')
        point=await page.evaluate('()=>{const p=map_div.latLngToContainerPoint([52.5,-2.9]);return [p.x+25,p.y]}')
        await page.mouse.click(*point)
        await page.locator('.leaflet-popup').wait_for()
        assert 'Position age:' in await page.locator('.leaflet-popup').inner_text()
        await page.screenshot(path=str(out/'traffic-fixture.png'))
        results['traffic']={'fixture_targets':5,'airborne_filter':4,'cold_start_reads':traffic_reads,'references_default_off':True,'rerun_events':0}
        if args.live_traffic:
            body=json.loads(Path(args.live_traffic).read_text())
            await page.locator('[data-ogn="refresh"]').click()
            await page.wait_for_timeout(500)
            count=await page.evaluate('map_div._duoTraffic.count')
            assert count>0,'Live capture has no unexpired targets; obtain a fresh capture.'
            results['traffic']['live_captured_targets']=count
            await page.evaluate('map_div.setView([53.1,-2.6],7,{animate:false})')
            await page.wait_for_timeout(200)
            await page.screenshot(path=str(out/'traffic-live.png'))
        body={'schema':2,'status':'degraded','source':'No live feed connected','fetched_at':now.isoformat(),'aircraft':[]}
        await page.locator('[data-ogn="refresh"]').click()
        await page.wait_for_function('map_div._duoTraffic.count===0')
        assert not await page.locator('[data-ogn="demo"]').count()
        results['traffic']['empty_feed_has_no_placeholder']=True
        assert not errors,errors
        results['errors']=errors;(out/'results.json').write_text(json.dumps(results,indent=2))
        print(json.dumps(results));await browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live-traffic');parser.add_argument('--live-wind');asyncio.run(main(parser.parse_args()))
