"""Optional Chromium regression check against an already running recorded app.

Use an isolated DUO_TASK_STORE: this check saves MYN–BIE–MYN on the selected day.
Requires the development-only playwright package and a Chromium executable.
"""
import argparse
import asyncio
import json
import time
import urllib.request
from pathlib import Path


async def verify(args):
    from playwright.async_api import async_playwright
    errors, failed, sent = [], [], []
    cache = {}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=args.chromium, headless=True, args=['--no-sandbox'])
        context = await browser.new_context(viewport={'width':1600, 'height':1500})
        if args.proxy_assets:
            async def asset(route):
                url = route.request.url
                def fetch():
                    if url not in cache:
                        request = urllib.request.Request(url, headers={'User-Agent':'DuoDiscusWeatherValidation/1.0'})
                        with urllib.request.urlopen(request, timeout=15) as response:
                            cache[url] = (response.status, response.headers.get('Content-Type','application/octet-stream'), response.read())
                    return cache[url]
                try:
                    status, mime, body = await asyncio.to_thread(fetch)
                    await route.fulfill(status=status, content_type=mime, body=body)
                except Exception as exc:
                    failed.append([url,str(exc)])
                    await route.abort()
            # Preserve TLS verification in managed environments whose browser
            # lacks the proxy's CA, using Python's trusted HTTPS client.
            await context.route('https://**/*', asset)
        page = await context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('websocket', lambda socket: socket.on('framesent', lambda _: sent.append(time.perf_counter())))
        await page.goto(args.url, wait_until='domcontentloaded')

        async def open_map(name, selector):
            await page.locator('.st-key-active_page').get_by_text(name,exact=True).click(timeout=90000)
            frame = page.locator('iframe[title="map_component.duo_map"]').last.content_frame
            await frame.locator(selector).wait_for(timeout=90000)
            root=frame.locator('.leaflet-container')
            assert await root.is_visible()
            assert (await root.bounding_box())['height']>=600
            await page.wait_for_timeout(700)
            return frame, root

        async def point_click(root, code):
            await root.evaluate("(el,code)=>{const m=window.map_div,p=m._duoBga.records.find(p=>p.code===code);m.setView([p.lat,p.lon],10,{animate:false});}",code)
            await page.wait_for_timeout(400)  # settle the setup pan, not a click delay
            xy = await root.evaluate("(el,code)=>{const m=window.map_div,p=m._duoBga.records.find(p=>p.code===code),q=m.latLngToContainerPoint([p.lat,p.lon]);return {x:q.x,y:q.y};}",code)
            box = await root.bounding_box()
            await page.mouse.click(box['x']+xy['x'],box['y']+xy['y'])
            await page.mouse.move(box['x']+xy['x']+80,box['y']+xy['y']-40)

        frame, root = await open_map('Task planner','[data-action="clear"]')
        await frame.locator('[data-action="clear"]').click()
        await page.wait_for_timeout(700)
        baseline = len(sent)
        await point_click(root,'MYN')
        await point_click(root,'BIE')
        await page.wait_for_timeout(100)
        result = {'task':await root.evaluate("el=>{const m=window.map_div,t=m._duoTask;return {points:t.points.map(p=>p.code),bga:m._duoBga.records.length,airfields:m._duoAirfields.records.length,rubber:t.rubber.getLatLngs().length,draw_ms:t.drawMs,cursor_ms:t.cursorMs,bga_ms:m._duoBga.lastDrawMs,airfields_ms:m._duoAirfields.lastDrawMs};}")}
        for name in ('Speed to Fly (STF kt & Thermal Activity)','Potential Flight Distance (PFD)','Thermal Lift (kt)'):
            await root.evaluate('(el,name)=>{const m=window.map_div;m.addLayer(m._duoLayers._layers.find(x=>x.name===name).layer);}',name)
        await page.wait_for_timeout(700)
        result['task']['draft_updates'] = len(sent)-baseline
        assert result['task']['points']==['MYN','BIE'] and result['task']['rubber']==2
        assert result['task']['bga']>=1390 and result['task']['airfields']>=1098
        assert result['task']['draft_updates']==2
        await page.screenshot(path=str(output/'task.png'),full_page=True)
        await point_click(root,'MYN')
        assert await root.evaluate('el=>window.map_div._duoTask.complete&&window.map_div._duoTask.rubber.getLatLngs().length===0')
        await page.get_by_text('BGA / SeeYou Task Sheet',exact=True).wait_for(timeout=60000)
        await page.wait_for_timeout(1600)
        result['task']['finish_updates'] = len(sent)-baseline-result['task']['draft_updates']
        assert result['task']['finish_updates']==1
        # Saving a second time catches nested Streamlit bridge wrappers.
        await frame.locator('[data-action="undo"]').click()
        await page.wait_for_timeout(400)  # allow the separate Undo state packet
        baseline = len(sent)
        await frame.locator('[data-action="finish"]').click()
        await page.wait_for_timeout(4000)
        assert len(sent)-baseline==1
        assert await root.evaluate('el=>window.map_div._duoTask.complete&&window.map_div._duoTask.points.length===2')
        await frame.locator('[data-action="home"]').click()
        await page.wait_for_timeout(4000)
        assert await root.evaluate("el=>window.map_div._duoTask.points.map(p=>p.code).join(',')==='MYN,BIE,MYN'")

        frame, root = await open_map('Wave watch','[data-wave-level="0"]')
        for _ in range(120):
            if await root.evaluate('el=>window.map_div._duoWave.ready'):break
            await page.wait_for_timeout(500)
        assert await root.evaluate('el=>window.map_div._duoWave.ready')
        await root.evaluate('el=>{window.map_div.setView([52.85,-3.5],8,{animate:false});}')
        await page.wait_for_timeout(800)
        baseline = len(sent)
        result['wave'] = {'levels':[]}
        for i in range(9):
            await frame.locator(f'[data-wave-level="{i}"]').click()
            result['wave']['levels'].append(await root.evaluate('el=>{const w=window.map_div._duoWave;return {altitude:w.data.frames[w.index].altitude,switch_ms:w.switchMs,render_ms:w.renderMs};}'))
        await frame.locator('[data-wave-level="3"]').click()
        await root.evaluate('el=>{window.map_div.on("click",e=>window._verifiedWaveClick={lat:e.latlng.lat,lon:e.latlng.lng});}')
        xy = await root.evaluate('el=>{const p=window.map_div.latLngToContainerPoint([52.92,-3.7]);return {x:p.x,y:p.y};}')
        box = await root.bounding_box()
        await page.mouse.click(box['x']+xy['x'],box['y']+xy['y'])
        await frame.locator('.duo-wave-section').wait_for(timeout=10000)
        result['wave']['clicked'] = await root.evaluate('el=>window._verifiedWaveClick')
        assert abs(result['wave']['clicked']['lat']-52.92)<.01
        assert abs(result['wave']['clicked']['lon']+3.7)<.01
        result['wave']['section_ms'] = await root.evaluate('el=>window.map_div._duoWave.sectionMs')
        result['wave']['regions'] = await root.evaluate("el=>window.map_div._duoWave.data.domains.map(d=>({name:d.name,terrain:window.map_div._duoWave.pointProfile(L.latLng(d.latitude,d.longitude)).terrain}))")
        assert len(result['wave']['regions'])==1
        assert await root.evaluate('el=>!window.map_div.hasLayer(window.map_div._duoBga)')
        result['wave']['coverage']=await root.evaluate("el=>{const w=window.map_div._duoWave;return [[53.07,-4.08,'Snowdonia'],[52.47,-3.78,'Cambrian'],[51.9,-3.4,'Brecon'],[52.5,-2.9,'Mynd'],[54.7,-2.48,'Pennines'],[54.55,-3.05,'Lakes'],[55.48,-2.15,'Cheviots'],[56.8,-5,'Highlands'],[57.2,-3.5,'Cairngorms'],[58,-5,'NW Highlands']].map(([lat,lon,name])=>{const p=w.pointProfile(L.latLng(lat,lon));return {name,terrain:p.terrain,values:p.values};});}")
        assert all(p['terrain'] is not None and any(v is not None for v in p['values']) for p in result['wave']['coverage'])

        await page.screenshot(path=str(output/'wave.png'),full_page=True)
        await frame.locator('.duo-section-close').click()
        await frame.locator('[data-wave-section]').check()
        await page.mouse.move(box['x']+box['width']*.45,box['y']+250)
        await page.mouse.down()
        await page.mouse.move(box['x']+box['width']*.7,box['y']+420,steps=10)
        await page.mouse.up()
        await frame.locator('.duo-wave-section').wait_for(timeout=5000)
        result['wave']['drag_section_ms'] = await root.evaluate('el=>window.map_div._duoWave.sectionMs')
        await frame.locator('[data-wave-section]').uncheck()
        await root.evaluate('el=>{window.map_div.panBy([30,20],{animate:false});}')
        await page.wait_for_timeout(700)
        result['wave']['updates'] = len(sent)-baseline
        assert result['wave']['updates']<=2

        frame, root = await open_map('Live OGN Glider Radar','[data-ogn="demo"]')
        await page.wait_for_timeout(500)
        baseline = len(sent)
        await frame.locator('[data-ogn="demo"]').click()
        await frame.locator('select[aria-label="OGN auto-refresh"]').select_option('15')
        await page.wait_for_timeout(1800)
        result['traffic'] = await root.evaluate('el=>({count:window.map_div._duoTraffic.count,mode:window.map_div._duoTraffic.status,draw_ms:window.map_div._duoTraffic.layer.lastDrawMs})')
        result['traffic']['updates'] = len(sent)-baseline
        assert result['traffic']['count']==7 and result['traffic']['mode']=='demo'
        assert result['traffic']['updates']==0
        result.update(errors=errors, failed_assets=failed)
        await page.locator('.st-key-active_page').get_by_text('Help & Briefing Bot',exact=True).click()
        chat=page.get_by_placeholder('Ask for a task, day briefing, turnpoint or explanation…')
        questions=["I want to fly from the Mynd to the Mynd North and Mynd South back to the Mynd",
                   "Could you give me a briefing for tomorrow at the Mynd with strong West winds and ridge lift?"]
        result['briefings']=[]
        for question in questions:
            messages=page.locator('[data-testid="stChatMessage"]')
            before_count=await messages.count()
            await chat.fill(question);await chat.press('Enter')
            for _ in range(180):
                if await messages.count()>=before_count+2:break
                await page.wait_for_timeout(500)
            answer=await messages.last.inner_text()
            assert 'THE — Thetford' not in answer
            assert 'Build or load a task first' not in answer
            assert ('Wind and ridge lift' in answer or 'Local synoptic and soaring summary' in answer)
            assert ('launch/landing hazards' in answer or 'Launch and landing checks' in answer)
            result['briefings'].append({'question':question,'answer':answer})
        assert 'MNDN' in result['briefings'][0]['answer'] and 'MNDS' in result['briefings'][0]['answer']
        frame,root=await open_map('Task planner','[data-action="clear"]')
        result['bot_route']=await root.evaluate('el=>window.map_div._duoTask.points.map(p=>p.code)')
        assert result['bot_route']==['MYN','MNDN','MNDS','MYN']
        await page.screenshot(path=str(output/'restored-task.png'),full_page=True)
        await page.locator('.st-key-active_page').get_by_text('Help & Briefing Bot',exact=True).click()
        await page.screenshot(path=str(output/'briefing.png'),full_page=True)
        # The recorded fixture has no UKMO data. Every map must still mount.
        await page.get_by_role('combobox',name='Forecast model',exact=True).click()
        await page.get_by_role('option',name='Met Office · UK 2 km',exact=True).click()

        result['missing_forecast_maps']={}
        for name,selector in [('Task planner','[data-action="clear"]'),('Wave watch','[data-wave-level="0"]'),('Live OGN Glider Radar','[data-ogn="demo"]')]:
            frame,root=await open_map(name,selector)
            result['missing_forecast_maps'][name]=await root.bounding_box()
            canvas=root.locator('canvas').first
            assert await canvas.is_visible()
            assert (await canvas.bounding_box())['height']>=600
        await page.screenshot(path=str(output/'missing-weather-map.png'),full_page=True)
        (output/'results.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
        assert not errors and not failed
        assert not await page.locator('[data-testid="stException"]').count()
        await browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8504')
    parser.add_argument('--chromium',default='/usr/bin/chromium')
    parser.add_argument('--output',default='var/client-map-validation')
    parser.add_argument('--proxy-assets',action='store_true',help='Fetch browser HTTPS assets using the trusted Python client.')
    asyncio.run(verify(parser.parse_args()))
