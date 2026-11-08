"""Exercise the production wind canvas in Chromium with deterministic weather.

No weather/OGN requests. Public Leaflet assets and optional terrain tiles use
the environment's trusted HTTPS proxy. Outputs go to var/wind-display-browser.
"""
import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.async_api import async_playwright, expect
from app import build_map
from map_weather import national_grid, wind_payload
from tests.fixtures import forecast


def document():
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    sources = tuple(forecast(p, start=now.replace(hour=0), hours=120)
                    for p in national_grid() if p.latitude < 56 and p.longitude > -7)
    product = wind_payload(sources, now)
    # Distinct wind at every height/hour proves the selectors still use the data.
    for station in product['stations']:
        for hour, levels in enumerate(station['values']):
            for level, vector in enumerate(levels):
                if vector:
                    vector[:2] = [10 + level * 3 + hour / 120, -level * 2]
    view, _ = build_map((), now, layers=[], visual_mode='wind', wind_product=product,
                        live_airfields=False)
    return view.get_root().render(), view.get_name()


async def main(args):
    html, name = document()
    out = Path('var/wind-display-browser'); out.mkdir(parents=True, exist_ok=True)
    errors, cache, results = [], {}, {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                         args=['--no-sandbox'])
        page = await browser.new_page(viewport={'width':1450, 'height':1000}, device_scale_factor=2)
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.add_init_script('''
          window.Streamlit={setComponentValue:()=>{}};
          window.windDraw={strokes:new Set(),fills:new Set(),rectangles:0};
          for(const method of ['stroke','fill','fillRect']){
            const original=CanvasRenderingContext2D.prototype[method];
            CanvasRenderingContext2D.prototype[method]=function(...args){
              if(this.canvas.classList.contains('duo-wind-particles')){
                if(method==='stroke')windDraw.strokes.add(this.strokeStyle+':'+Number(this.lineWidth.toFixed(3)));
                if(method==='fill')windDraw.fills.add(this.fillStyle);
                if(method==='fillRect')windDraw.rectangles++;
              }
              return original.apply(this,args);
            };
          }
        ''')
        async def asset(route):
            url = route.request.url
            if not args.terrain and ('/tile/' in url or '/tiles/' in url or 'tile.openstreetmap' in url):
                await route.abort(); return
            def fetch():
                if url not in cache:
                    with urllib.request.urlopen(url, timeout=15) as response:
                        cache[url] = (response.headers.get_content_type(), response.read())
                return cache[url]
            try:
                mime, raw = await asyncio.to_thread(fetch)
                await route.fulfill(content_type=mime, body=raw)
            except Exception:
                await route.abort()
        await page.route('https://**/*', asset)
        await page.route('http://duo.test/**', lambda route: route.fulfill(content_type='text/html', body=html))
        await page.goto('http://duo.test/wind.html', wait_until='domcontentloaded')
        await page.wait_for_function(f'window.{name}?._duoWind?.renderStats.arrowCount>0')
        await page.evaluate(f'()=>{{window.map_div=window.{name};window.events=[];window.duoMapEvent=(...args)=>events.push(args);}}')
        assert await page.evaluate('map_div._duoWind.display') == 'arrows'
        assert await page.locator('.duo-wind-heat,.duo-wind-raster').count() == 0
        coverage = await page.evaluate('''()=>{
          const w=map_div._duoWind;let count=0;
          for(const [lat,lon] of [[50.5,-3.5],[52.5,-2.9],[54.5,-2],[57.2,-3.8],[53.3,-7.5]])
            for(let hour=0;hour<120;hour++)for(let level=0;level<6;level++){
              const v=w.sampleAt(lat,lon,hour,level);if(v&&Number.isFinite(v.u)&&Number.isFinite(v.v))count++;
            }
          return count;
        }''')
        assert coverage == 3600
        await page.evaluate('map_div.fitBounds([[49.5,-11],[61,2.5]],{animate:false})')
        await page.wait_for_timeout(150)
        quadrants = await page.evaluate('''()=>{
          const q=[0,0,0,0],w=map_div._duoWind;
          for(const p of w.swarm){const ll=map_div.containerPointToLatLng([p.x,p.y]);
            if(w.sampleAt(ll.lat,ll.lng))q[(ll.lat>55.25?2:0)+(ll.lng> -4.25?1:0)]++;}return q;
        }''')
        assert all(n > 5 for n in quadrants), quadrants
        await page.evaluate('map_div.setView([52.5,-2.9],7,{animate:false})')
        if args.terrain:
            await page.wait_for_function('Object.values(map_div._layers).filter(l=>l instanceof L.TileLayer).every(l=>!l._loading)', timeout=30000)
            assert await page.locator('img.leaflet-tile-loaded').count() >= 4
        await page.wait_for_timeout(350)
        pixels = await page.evaluate('''()=>{
          const c=document.querySelector('.duo-wind-particles'),a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
          let black=0,clear=0,coloured=0;
          for(let i=0;i<a.length;i+=4){if(!a[i+3])clear++;
            if(a[i+3]>200&&Math.abs(a[i]-17)<3&&Math.abs(a[i+1]-24)<3&&Math.abs(a[i+2]-39)<3)black++;
            if(a[i+3]>64&&a[i+2]-a[i]>30)coloured++;}
          return {black,transparent:clear/(a.length/4),coloured,filter:getComputedStyle(c).filter};
        }''')
        assert pixels['black'] > 100 and pixels['transparent'] > .9, pixels
        assert pixels['coloured'] == 0 and pixels['filter'] == 'none', pixels
        strokes = await page.evaluate('[...windDraw.strokes]')
        assert '#111827:2.4' in strokes and 'rgba(255, 255, 255, 0.55):4' in strokes, strokes
        assert await page.evaluate('[...windDraw.fills]') == ['#111827']
        assert await page.evaluate('windDraw.rectangles') == 0
        await page.screenshot(path=str(out/'streaming-arrows.png'))

        async def wind_text():
            # Leaflet retains the previous popup while its fade-out finishes.
            popup = page.locator('.duo-callout .leaflet-popup-content')
            await expect(popup).to_have_count(1)
            return await popup.text_content()

        async def inspect():
            # A real pointer click, not a synthetic Leaflet event.
            point = await page.evaluate('''()=>{const p=map_div.latLngToContainerPoint([52.5,-2.9]),r=map_div.getContainer().getBoundingClientRect();return [p.x+r.x,p.y+r.y];}''')
            await page.mouse.click(*point)
            await page.locator('.duo-callout').last.wait_for()
            await wind_text()
            await page.get_by_text('Vertical wind profile', exact=True).click()
            assert await page.locator('.duo-callout tr').count() == 6
            assert await page.locator('.duo-bars i').count() == 120
            return await wind_text()

        first = await inspect()
        assert 'Wind: 20 kt' in first, first
        await page.locator('[data-level="3"]').click()
        elevated = await wind_text()
        assert 'Wind: 39 kt' in elevated and elevated != first, elevated
        await page.locator('[data-level="0"]').click()
        await wind_text()
        times = []
        async def toggle(display):
            elapsed = await page.evaluate('''(display)=>{const start=performance.now();document.querySelector('[data-wind-view="'+display+'"]').click();return performance.now()-start;}''',display)
            assert elapsed < 300, elapsed
            times.append(elapsed)
        await toggle('barbs')
        assert await page.locator('[data-wind-view=barbs]').get_attribute('aria-pressed') == 'true'
        stats = await page.evaluate('map_div._duoWind.renderStats')
        assert stats['barbCount'] > 10 and stats['arrowCount'] == 0 and not stats['animationRunning'], stats
        strokes = await page.evaluate('[...windDraw.strokes]')
        assert '#111827:2' in strokes and 'rgba(255, 255, 255, 0.55):3.6' in strokes, strokes
        # Popup auto-pan can rebuild once; after settling barbs must stop drawing.
        await page.wait_for_timeout(400)
        frames = await page.evaluate('map_div._duoWind.renderStats.drawFrames')
        await page.wait_for_timeout(200)
        assert await page.evaluate('map_div._duoWind.renderStats.drawFrames') == frames
        await page.locator('.leaflet-popup-close-button').click()
        assert await inspect() == first
        await page.locator('[data-level="3"]').click()
        assert await wind_text() == elevated
        await page.locator('#duo-wind-time').fill('119')
        assert 'Wind: 41 kt' in await wind_text()
        await page.locator('[data-level="0"]').click()
        await wind_text()
        await page.locator('.leaflet-popup-close-button').click()
        await expect(page.locator('.duo-callout')).to_have_count(0)
        await page.wait_for_timeout(350)
        await page.screenshot(path=str(out/'wind-barbs.png'))
        spacing = []
        for zoom in (5, 7, 9):
            await page.evaluate('(z)=>map_div.setZoom(z,{animate:false})', zoom)
            spacing.append(await page.evaluate('map_div._duoWind.renderStats.spacing'))
            assert await page.evaluate('map_div._duoWind.barbs.every(b=>map_div._duoWind.renderStats.spacing>=2*b.parts.shaft+44)')
        assert spacing[0] < spacing[-1], spacing
        # Simulate an old heat canvas retained by a hot deployment.
        await page.evaluate('''()=>{map_div.setZoom(6,{animate:false});const c=document.createElement('canvas');c.className='duo-wind-heat';c.style.background='blue';map_div.getContainer().appendChild(c);map_div.setZoom(7,{animate:false});}''')
        assert await page.locator('.duo-wind-heat').evaluate('(c)=>getComputedStyle(c).display') == 'none'
        await toggle('off')
        assert not await page.evaluate('map_div._duoWind.renderStats.animationRunning')
        assert await page.evaluate('map_div._duoWind.display')=='off'
        await toggle('arrows')
        assert await page.evaluate('map_div._duoWind.renderStats.animationRunning')
        assert await page.evaluate('map_div._duoWind.renderStats.arrowCount') > 0
        await page.emulate_media(reduced_motion='reduce')
        await page.wait_for_function('!map_div._duoWind.renderStats.animationRunning')
        assert await page.evaluate('map_div._duoWind.renderStats.arrowCount') > 0
        assert await page.evaluate('events.length') == 0
        assert not errors, errors
        await page.evaluate('map_div._duoCleanup()')
        assert await page.locator('.duo-wind-particles,[data-wind-view]').count() == 0
        # A missing/malformed upstream payload must never remove the toolbar.
        for missing in (None,{'grid':None,'stations':[None,{}],'levels':None}):
            await page.evaluate('(wind)=>installDuoModes(map_div,{mode:"wind",when:"2026-11-08T12:00:00Z",wind,samples:null,contours:null,modes:null})',missing)
            assert await page.locator('[data-wind-view]').count()==3
            assert await page.locator('[data-level]').count()==6
            assert await page.evaluate('map_div._duoWind.sampleAt(53,-3)') is None
            for mode in ('barbs','off','arrows'):
                await page.locator('[data-wind-view='+mode+']').click()
                assert await page.evaluate('map_div._duoWind.display')==mode
            await page.evaluate('map_div._duoCleanup()')
        assert not errors,errors
        results = {'coverage_checks':coverage, 'quadrants':quadrants, 'canvas_pixels':pixels,
                   'barbs':stats['barbCount'], 'barb_spacing':spacing, 'toggle_ms':times,
                   'altitude_and_timeline_in_both_modes':True, 'rerun_events':0,
                   'reduced_motion':True, 'missing_payload_controls':True, 'errors':errors, 'terrain_tiles':args.terrain}
        (out/'results.json').write_text(json.dumps(results, indent=2)+'\n')
        print(json.dumps(results)); await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--terrain', action='store_true')
    asyncio.run(main(parser.parse_args()))
