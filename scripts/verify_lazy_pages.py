"""Browser regression: lazy pages, canonical tasks, fragment saves and local chat."""
import asyncio,json,time
from pathlib import Path
from playwright.async_api import async_playwright,expect

async def main():
    out=Path('var/lazy-browser');out.mkdir(exist_ok=True)
    result={'pages':{},'errors':[]};sent=[];received=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1600,'height':1500})
        await page.route('https://**/*',lambda r:r.abort())
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        page.on('websocket',lambda ws:(ws.on('framesent',lambda f:sent.append(len(f))),ws.on('framereceived',lambda f:received.append(len(f)))))
        await page.goto('http://127.0.0.1:8504')
        nav=page.locator('.st-key-active_page')
        await nav.get_by_role('radio',name='Weather map',exact=True).wait_for(timeout=90000)
        frame=lambda:page.locator('iframe[title="map_component.duo_map"]').last.content_frame
        root=lambda:frame().locator('.leaflet-container')
        async def choose(name,marker=None):
            before=time.perf_counter();await nav.get_by_text(name,exact=True).click()
            if marker:await frame().locator(marker).wait_for(timeout=90000)
            else:
                heading={'Data quality':'Forecast quality'}.get(name,name)
                await page.get_by_role('heading',name=heading,exact=True).wait_for(timeout=30000)
            await expect(page.locator('iframe[title="map_component.duo_map"]')).to_have_count(1 if marker else 0,timeout=30000)
            result['pages'][name]={'ready_ms':round((time.perf_counter()-before)*1000),'iframes':await page.locator('iframe[title="map_component.duo_map"]').count()}
            assert not await page.locator('[data-testid="stException"]').count()
        await choose('Task planner','[data-action="clear"]')
        await frame().locator('[data-action="clear"]').click();await page.wait_for_timeout(350)
        await expect(page.get_by_test_id('stApp')).not_to_have_attribute('data-test-script-state','running',timeout=30000)
        await page.wait_for_timeout(150)  # apply the final render message
        # The same browser map object survives fragment-only saves.
        await root().evaluate("el=>{window.map_div._testIdentity='unchanged';window.map_div.setView([52.52,-2.88],10,{animate:false});}")
        async def add(code):
            await root().evaluate("(el,code)=>{const m=window.map_div;m._duoTask.add(m._duoBga.records.find(p=>p.code===code));}",code)
            await page.wait_for_timeout(300)
        start=len(received);await add('MYN');await add('BIE')
        assert await root().evaluate("el=>window.map_div._testIdentity==='unchanged'")
        saved=await root().evaluate("el=>JSON.parse(localStorage.getItem('duo_active_task_v1'))")
        assert [p['code'] for p in saved['points']]==['MYN','BIE'],saved
        result['fragment_frames_bytes']=received[start:]
        await choose('Weather map','[data-clear-task]')
        route=await root().evaluate('el=>window.map_div._duoShared.state.points.map(p=>p.code)')
        assert route==['MYN','BIE'],route
        await frame().locator('[data-clear-task]').click();await page.wait_for_timeout(350)
        assert await root().evaluate("el=>localStorage.getItem('duo_active_task_v1')===null&&window.map_div._duoShared.state.points.length===0")
        await choose('Task planner','[data-action="clear"]')
        assert await root().evaluate('el=>window.map_div._duoTask.points.length===0')
        await add('MYN');await add('CVN')
        await frame().locator('[data-action="clear"]').click();await page.wait_for_timeout(350)
        await choose('Weather map','[data-clear-task]')
        assert await root().evaluate('el=>window.map_div._duoShared.state.points.length===0')
        result['task_sync']='add, cross-page, clear from both toolbars: passed'
        for name in ('Help Guide','Data quality','Help & Briefing Bot'):
            await choose(name)
            assert await page.locator('iframe[title="map_component.duo_map"]').count()==0,name
        chat=page.get_by_placeholder('Ask for a task, day briefing, turnpoint or explanation…')
        await chat.fill('What are the soaring conditions at Midland Gliding Club only within a 20 km sector?');await chat.press('Enter')
        await page.get_by_text('Local synoptic and soaring summary',exact=True).wait_for(timeout=30000)
        answer=await page.locator('[data-testid="stChatMessage"]').last.inner_text()
        assert '20 km' in answer and 'Base ft AGL' in answer,answer
        for unwanted in ('Where the better sectors lie','Five-day outlook','Goole','Didcot','Your task · leg tactics'):
            assert unwanted not in answer,unwanted
        result['local_briefing']=answer
        await choose('Wave watch','[data-wave-level="0"]')
        await page.wait_for_function("() => true")
        for _ in range(100):
            if await root().evaluate('el=>window.map_div._duoWave.ready'):break
            await page.wait_for_timeout(100)
        assert await root().evaluate('el=>window.map_div._duoWave.ready')
        await frame().locator('[data-wave-level="3"]').click()
        assert result['pages']['Wave watch']['iframes']==1
        cold_wave=result['pages']['Wave watch']['ready_ms']
        await choose('Help Guide');assert result['pages']['Help Guide']['iframes']==0
        await choose('Wave watch','[data-wave-level="0"]')
        result['cold_wave_ready_ms']=cold_wave
        await choose('Help Guide')
        await page.screenshot(path=str(out/'help.png'),full_page=True)
        assert not result['errors'],result['errors']
        (out/'results.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='local_briefing'}))
        await browser.close()

if __name__=='__main__':asyncio.run(main())
