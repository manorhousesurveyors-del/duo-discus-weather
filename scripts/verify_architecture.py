"""Production pages/controls against healthy, rate-limited and slow HTTP fixtures.

Start architecture_fixture.py with DUO_TEST_SCENARIO and a private task store.
Warm server timings exclude initial imports, browser tile I/O and chart prep.
"""
import argparse,asyncio,json,re,time,os,shutil
from pathlib import Path
from playwright.async_api import async_playwright,expect

PAGES={'Weather map':None,'Task planner':'Task planning map','Help Guide':'Help Guide',
       'Wave watch':'Wave Watch Map','Live OGN Glider Radar':'Live OGN Glider Radar',
       'Help & Briefing Bot':'Help & Briefing Bot','Data quality':'Forecast quality'}

async def main(args):
    out=Path('var/architecture-browser')/args.scenario;out.mkdir(parents=True,exist_ok=True)
    result={'scenario':args.scenario,'pages':{},'errors':[]}
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1650,'height':1450})
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        await page.route('https://**/*',lambda r:r.abort())
        await page.goto(args.url)
        nav=page.locator('.st-key-active_page')
        await nav.wait_for(timeout=60000)
        async def settle():
            await expect(page.get_by_test_id('stApp')).not_to_have_attribute('data-test-script-state','running',timeout=60000)
            await page.get_by_text('Fixture server render ms:',exact=False).wait_for(timeout=60000)
            await page.wait_for_timeout(100)
            assert not await page.get_by_test_id('stException').count(),await page.get_by_test_id('stException').all_text_contents()
        await settle()
        model=page.get_by_role('combobox',name='Forecast model',exact=True)
        await expect(model).to_have_value('DWD ICON-EU · 7 km')
        frames=page.locator('iframe[title="map_component.duo_map"]')
        frame=lambda:frames.last.content_frame
        root=lambda:frame().locator('.leaflet-container')
        if args.scenario=='healthy':
            await expect(page.get_by_text('8/8 local hours covered',exact=False)).to_have_count(5,timeout=60000)
        else:
            await expect(page.get_by_text('Awaiting live weather',exact=True)).to_have_count(5)
            assert not await page.get_by_text('8/8 local hours covered',exact=False).count()
        await page.get_by_role('combobox',name='Map mode',exact=True).click()
        await page.get_by_role('option',name='Windy-style Wind Stream',exact=True).click()
        await frame().locator('[data-wind-view=arrows]').wait_for(timeout=60000)
        if args.scenario=='healthy':
            for _ in range(100):
                if await root().evaluate('el=>!!window.map_div._duoWind.sampleAt(52.5,-2.9)'):break
                await page.wait_for_timeout(200)
            assert await root().evaluate('el=>!!window.map_div._duoWind.sampleAt(52.5,-2.9)')
        assert not await frame().locator('.duo-wind-heat,.duo-wind-raster').count()
        await expect(frame().locator('[data-reference=bga]')).not_to_be_checked()
        wind=[]
        for mode in ('barbs','off','arrows'):
            ms=await root().evaluate('(el,mode)=>{const start=performance.now();document.querySelector(`[data-wind-view="${mode}"]`).click();return performance.now()-start;}',mode)
            assert await root().evaluate('el=>window.map_div._duoWind.display')==mode
            assert ms<100,ms;wind.append(round(ms,2))
        await frames.last.scroll_into_view_if_needed()
        box=await root().bounding_box()
        xy=await root().evaluate('el=>{const p=window.map_div.latLngToContainerPoint([53.2,-4.5]);return [p.x,p.y];}')
        await page.mouse.click(box['x']+xy[0],box['y']+xy[1])
        await frame().locator('.duo-callout').last.wait_for()
        await expect(frame().locator('.duo-callout').last).to_contain_text('Wind')
        await frame().locator('[data-level="3"]').click()
        result['wind_controls_ms']=wind
        # Record baseline after background grid assembly has settled.
        await page.wait_for_timeout(3000);await settle()
        for iteration in range(2):
            for name,heading in PAGES.items():
                start=time.perf_counter()
                await nav.get_by_text(name,exact=True).click()
                if heading:await page.get_by_role('heading',name=heading,exact=True).wait_for(timeout=60000)
                await settle()
                for page_name,title in (('Task planner','Task planning map'),('Wave watch','Wave Watch Map')):
                    await expect(page.get_by_role('heading',name=title,exact=True)).to_have_count(int(name==page_name))
                await expect(frames).to_have_count(0 if name in ('Help Guide','Help & Briefing Bot','Data quality') else 1)
                if name!='Weather map':assert not await page.get_by_text('Enable Point Forecast Probe',exact=True).count()
                ms=float((await page.get_by_text('Fixture server render ms:',exact=False).inner_text()).split(':')[-1])
                if iteration:
                    result['pages'][name]={'server_ms':ms,'browser_ready_ms':round((time.perf_counter()-start)*1000,1)}
                    assert ms<500,(name,ms)
        await nav.get_by_text('Weather map',exact=True).click();await settle()
        # Changes slice the same bundle; transport request count remains stable.
        before=await page.get_by_text('Fixture HTTP requests:',exact=False).inner_text()
        day=page.get_by_role('combobox',name='Forecast day',exact=True)
        await day.click();await page.get_by_role('option',name=re.compile('Day 2 ·')).click();await settle()
        after=await page.get_by_text('Fixture HTTP requests:',exact=False).inner_text()
        assert before==after,(before,after)
        await page.get_by_text('Enable Point Forecast Probe',exact=True).click()
        await frame().locator('.duo-probe-prompt').wait_for(timeout=60000)
        # Product generation is background; clicks are local canvas work.
        await page.wait_for_timeout(3000);await settle()
        await root().evaluate('el=>window.map_div._duoProbe.show({lat:52.518,lon:-2.88,name:"Long Mynd"})')
        await frame().locator('.duo-point-panel').wait_for()
        probe=await root().evaluate('el=>({ms:window.map_div._duoProbe.lastRenderMs,paint:window.map_div._duoProbe.lastPaintMs,rows:window.map_div._duoProbe.last.rows.length})')
        assert probe['ms']<100,probe
        result['probe']=probe;result['day_change_request_count']=after
        await page.screenshot(path=str(out/'weather-probe.png'),full_page=True)
        assert not result['errors'],result['errors']
        (out/'results.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result));await browser.close()

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--scenario',choices=['healthy','429','timeout'],default='healthy');ap.add_argument('--url',default='http://127.0.0.1:8508')
    asyncio.run(main(ap.parse_args()))
