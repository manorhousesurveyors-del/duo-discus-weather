"""Physical mouse regression for magnetic snapping, elastic edits and insertion."""
import asyncio,json,urllib.request
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    out=Path('var/route-drag-browser');out.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        context=await browser.new_context(viewport={'width':1600,'height':1500});cache={};errors=[];packets=[]
        async def asset(route):
            url=route.request.url
            def fetch():
                if url not in cache:
                    with urllib.request.urlopen(url,timeout=20) as r:cache[url]=(r.headers.get('Content-Type','application/octet-stream'),r.read())
                return cache[url]
            try:
                mime,data=await asyncio.to_thread(fetch);await route.fulfill(content_type=mime,body=data)
            except Exception:await route.abort()
        await context.route('https://**/*',asset)
        page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('websocket',lambda w:w.on('framesent',lambda _:packets.append(1)))
        await page.goto('http://127.0.0.1:8504')
        await page.locator('.st-key-active_page').get_by_text('Task planner',exact=True).click(timeout=90000)
        frame=page.locator('iframe[title="map_component.duo_map"]').last.content_frame
        root=frame.locator('.leaflet-container');await frame.locator('[data-action="clear"]').wait_for(timeout=90000)
        await frame.locator('[data-action="clear"]').click()
        await root.evaluate("el=>{const m=window.map_div;m.setView([52.5,-2.9],11,{animate:false});m._duoTask.add(m._duoBga.records.find(p=>p.code==='MYN'));}")
        await page.wait_for_timeout(900)
        async def xy(code,dx=0):
            q=await root.evaluate("(el,code)=>{const m=window.map_div,p=m._duoBga.records.find(p=>p.code===code),q=m.latLngToContainerPoint([p.lat,p.lon]);return {x:q.x,y:q.y};}",code)
            b=await root.bounding_box();return b['x']+q['x']+dx,b['y']+q['y']
        x,y=await xy('CVN',15);baseline=len(packets)
        await page.mouse.move(x,y);await page.wait_for_timeout(150)
        snapped=await root.evaluate("el=>{const m=window.map_div,p=m._duoBga.records.find(p=>p.code==='CVN'),ll=m._duoTask.rubber.getLatLngs().at(-1);return m.distance(ll,[p.lat,p.lon]);}")
        assert snapped<1,snapped
        await page.mouse.click(x,y);await page.wait_for_timeout(200)
        assert await root.evaluate("el=>window.map_div._duoTask.points.map(p=>p.code).join(',')==='MYN,CVN'")
        assert len(packets)==baseline+1
        await frame.locator('[data-action="home"]').click();await page.wait_for_timeout(3500)
        async def drag(selector,target,expected):
            marker=frame.locator(selector);await marker.scroll_into_view_if_needed();box=await marker.bounding_box()
            x,y=await xy(target,12);baseline=len(packets)
            await page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);await page.mouse.down()
            await page.mouse.move(x,y,steps=20);await page.wait_for_timeout(150)
            during=await root.evaluate("el=>({points:window.map_div._duoTask.points.map(p=>p.code),drag_ms:window.map_div._duoTask.dragMs,dragging:window.map_div._duoTask.dragging})")
            assert during['dragging'],during;assert during['points']==expected,during
            assert len(packets)==baseline
            await page.mouse.up();await page.wait_for_timeout(4500)
            after=await root.evaluate('el=>window.map_div._duoTask.points.map(p=>p.code)')
            assert after==expected,after
            assert len(packets)-baseline==1,(len(packets),baseline)
            return during
        # Replace a middle point, insert a new one, then move the linked Start/Finish.
        results={'snap_error_m':snapped}
        results['replace']=await drag('.duo-task-handle [data-route-index="1"]','BCS',['MYN','BCS','MYN'])
        results['insert']=await drag('.duo-mid-handle [data-route-index="1"]','CVN',['MYN','CVN','BCS','MYN'])
        results['endpoints']=await drag('.duo-task-handle [data-route-index="3"]','CNN',['CNN','CVN','BCS','CNN'])
        results['errors']=errors;assert not errors,errors
        await page.screenshot(path=str(out/'elastic-task.png'),full_page=True)
        (out/'results.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results))
        await browser.close()

if __name__=='__main__':asyncio.run(main())
