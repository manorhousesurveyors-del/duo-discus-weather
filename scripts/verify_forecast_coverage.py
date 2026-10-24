"""Actual dropdowns and canvas probe for all 15 model/day combinations.

Run the coverage fixture in Streamlit on port 8506. Browser network is blocked
apart from localhost; no notifications or public provider calls are made.
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright, expect


async def main():
    out=Path('var/coverage-browser');out.mkdir(parents=True,exist_ok=True)
    results=[];errors=[];external=[];sent=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1700,'height':1400})
        await page.route('https://**/*',lambda route:(external.append(route.request.url),route.abort())[1])
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('websocket',lambda ws:ws.on('framesent',lambda frame:sent.append(frame)))
        await page.goto('http://127.0.0.1:8506')
        iframe=page.locator('iframe[title="map_component.duo_map"]').last
        frame=lambda:iframe.content_frame
        root=lambda:frame().locator('.leaflet-container')
        async def idle():
            await expect(page.get_by_test_id('stApp')).not_to_have_attribute('data-test-script-state','running',timeout=90000)
            await page.wait_for_timeout(500)  # Allow browser document acknowledgements.
        async def select(label,option):
            await page.get_by_role('combobox',name=label,exact=True).click()
            await page.get_by_role('option',name=option,exact=True).click()
            await idle()
        await root().wait_for(timeout=90000);await idle()
        for model,label in [('ukmo','Met Office · UKV / seamless'),('ifs025','ECMWF · medium range'),('icon_eu','DWD ICON-EU · 7 km')]:
            await select('Forecast model',label)
            if not await page.locator('.st-key-point_probe_enabled input').is_checked():
                await page.get_by_text('Enable Point Forecast Probe',exact=True).click()
            await frame().locator('.duo-probe-prompt').wait_for(timeout=60000);await idle()
            for day,date in enumerate(('Sat 24 Oct','Sun 25 Oct','Mon 26 Oct','Tue 27 Oct','Wed 28 Oct')):
                await select('Forecast day',f'Day {day} · {date}')
                await frame().locator('.duo-probe-prompt').wait_for(timeout=60000)
                await idle()
                assert await page.get_by_text('8/8 local hours covered',exact=False).count()==5
                assert await page.locator('[data-testid="stMetricValue"]').filter(has_text='★').count()==5
                text=await page.locator('body').inner_text()
                assert 'No regional forecast is available' not in text and 'Insufficient forecast coverage' not in text
                await iframe.scroll_into_view_if_needed()
                await root().evaluate('el=>window.map_div.setView([52.518,-2.880],10,{animate:false})')
                await page.wait_for_timeout(100)
                box=await root().bounding_box()
                point=await root().evaluate('el=>{const p=window.map_div.latLngToContainerPoint([52.518,-2.880]);return [p.x,p.y]}')
                before=len(sent);network=len(external)
                await page.mouse.click(box['x']+point[0],box['y']+point[1])
                await frame().locator('.duo-point-panel').wait_for()
                await page.wait_for_timeout(100)
                result=await root().evaluate('''el=>{const p=window.map_div._duoProbe;return {ms:p.lastRenderMs,paint:p.lastPaintMs,
                    rows:p.last.rows.slice(0,12).filter(r=>r.label>='11:30'&&r.label<='14:30').map(r=>({day:r.day,time:r.label,climb:r.climb,agl:r.agl,sources:r.sources}))}}''')
                assert result['rows'] and all(r['climb']>0 and r['agl']>0 for r in result['rows']),result
                assert all(r['day']==date for r in result['rows']),result
                assert result['ms']<100 and result['paint']<100,result
                assert len(sent)==before,'Probe caused a Streamlit rerun'
                assert len(external)==network,'Probe made a network request'
                results.append({'model':model,'day':day,**result})
                if day==4:await page.screenshot(path=str(out/(model+'.png')),full_page=True)
                await frame().locator('[data-probe-another]').click()
        assert not errors,errors
        (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps({'cases':len(results),'max_draw_ms':max(r['ms'] for r in results),'max_paint_ms':max(r['paint'] for r in results),'errors':errors}))
        await browser.close()


if __name__=='__main__':asyncio.run(main())
