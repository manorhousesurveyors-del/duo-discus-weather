"""Live integration check with an automatically started, isolated Streamlit server.

Weather is synthetic in that isolated fixture; OGN telemetry is genuine. No
notifications are sent. Public APRS may be disabled by the environment proxy.
"""
import asyncio
from datetime import datetime
import json
import os
import subprocess
import tempfile
from pathlib import Path
import time
import urllib.request
from playwright.async_api import async_playwright, expect


async def main():
    output=Path('var/ogn-stream-app-browser');output.mkdir(parents=True,exist_ok=True)
    errors=[];cache={};events=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1700,'height':1450})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('websocket',lambda socket:socket.on('framesent',lambda frame:events.append(frame)))
        async def asset(route):
            url=route.request.url
            def fetch():
                if url not in cache:
                    with urllib.request.urlopen(url,timeout=10) as r:cache[url]=(r.headers.get_content_type(),r.read())
                return cache[url]
            try:
                mime,raw=await asyncio.to_thread(fetch);await route.fulfill(content_type=mime,body=raw)
            except Exception:await route.abort()
        await page.route('https://**/*',asset)
        await page.goto('http://127.0.0.1:8507')
        await expect(page.get_by_text('8/8 local hours covered',exact=False)).to_have_count(5,timeout=45000)
        await expect(page.get_by_role('combobox',name='Forecast model',exact=True)).to_have_value('DWD ICON-EU · 7 km')
        async def snapshot():
            response=await page.request.get('http://127.0.0.1:8507/app/static/ogn/uk.json')
            return await response.json() if response.ok else {}
        before={}
        for _ in range(40):
            before=await snapshot()
            if before.get('schema')==2 and before.get('target_count',0)>0:break
            await page.wait_for_timeout(500)
        assert before.get('target_count',0)>0,before
        assert await page.locator('.st-key-active_page input:checked').count()==1
        # Leave the receiver on a text page through a full HTTPS acquisition cycle.
        await page.locator('.st-key-active_page').get_by_text('Help Guide',exact=True).click()
        await page.wait_for_timeout(18000)
        after=await snapshot()
        assert after['http_last_success']>before['http_last_success'],(before,after)
        await page.locator('.st-key-active_page').get_by_text('Live OGN Glider Radar',exact=True).click()
        frame=page.locator('iframe[title="map_component.duo_map"]').last.content_frame
        root=frame.locator('.leaflet-container')
        await frame.locator('[data-ogn="refresh"]').wait_for(timeout=45000)
        for _ in range(30):
            count=await root.evaluate('el=>window.map_div._duoTraffic.count')
            if count:break
            await page.wait_for_timeout(500)
        assert count>0
        assert not await frame.locator('[data-ogn="demo"]').count()
        assert not await root.evaluate('el=>map_div.hasLayer(map_div._duoBga)||map_div.hasLayer(map_div._duoAirfields)')
        colours=await root.evaluate('el=>[...new Set(map_div._duoTraffic.layer.records.map(p=>p.colour))].sort()')
        assert colours==['#38BDF8','#FDE047'],colours
        status=await frame.locator('.duo-ogn-state').inner_text()
        assert 'live.glidernet.org XML' in status,status
        await frame.locator('[data-ogn-filter="other"]').uncheck()
        assert await root.evaluate("el=>map_div._duoTraffic.layer.records.every(p=>p.category==='glider')")
        await frame.locator('[data-ogn-filter="other"]').check()
        await frame.locator('[data-reference="bga"]').check()
        assert await root.evaluate('el=>map_div.hasLayer(map_div._duoBga)')
        await frame.locator('[data-reference="bga"]').uncheck()
        await page.screenshot(path=str(output/'live-radar.png'),full_page=True)
        assert not await page.get_by_test_id('stException').count()
        assert not errors,errors
        result={'boot_page':'Weather map','boot_targets':before['target_count'],
                'continued_on_help':True,'before_http':before['http_last_success'],'after_http':after['http_last_success'],
                'radar_targets':count,'source_status':status,'colours':colours,'default_icon_coverage':[8]*5,
                'aprs_status':after['aprs_status'],'errors':errors}
        (output/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
        await browser.close()


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    # Streamlit serves static/ relative to its entry script, so use a temporary
    # root entrypoint (the shared fixture itself lives in scripts/).
    with tempfile.NamedTemporaryFile('w',dir=root,prefix='._ogn_smoke_',suffix='.py',delete=False) as f:
        f.write("from pathlib import Path\nexec(Path('scripts/fresh_forecast_fixture.py').read_text(), {'__name__':'__main__', '__file__':str(Path('scripts/fresh_forecast_fixture.py').resolve())})\n")
        entry=Path(f.name)
    log=(root/'var/ogn-stream-smoke-server.log').open('w')
    server=subprocess.Popen([str(root/'.venv/bin/python'),'-m','streamlit','run',str(entry),'--server.port','8507'],
        cwd=root,env={**os.environ,'DUO_OGN_ENABLED':'1','DUO_TASK_STORE':str(root/'var/ogn-stream-smoke-tasks')},stdout=log,stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            if server.poll() is not None:raise RuntimeError('Streamlit fixture failed to start')
            try:
                with urllib.request.urlopen('http://127.0.0.1:8507/_stcore/health',timeout=.5) as response:
                    if response.status==200:break
            except OSError:time.sleep(.2)
        asyncio.run(main())
    finally:
        server.terminate()
        try:server.wait(timeout=8)
        except subprocess.TimeoutExpired:server.kill();server.wait()
        log.close();entry.unlink(missing_ok=True)
