"""Browser verification against the explicitly mocked provider app on port 8505."""
import asyncio,json,re
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    out=Path('var/gemini-browser');out.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1500,'height':1200})
        # Maps use bundled libraries; this test needs no public tile requests.
        await page.route('https://**/*',lambda route:route.abort())
        await page.goto('http://127.0.0.1:8505')
        await page.get_by_role('tab',name='Help & Briefing Bot',exact=True).click(timeout=90000)
        panel=page.get_by_role('tabpanel',name='Help & Briefing Bot',exact=True)
        await panel.get_by_text('Gemini settings · connection and models',exact=True).click()
        await panel.get_by_test_id('stAlertContentSuccess').get_by_text('Connected to Gemini (gemini-3.1-pro-preview)',exact=True).wait_for(timeout=60000)
        chat=page.get_by_placeholder('Ask for a task, day briefing, turnpoint or explanation…')
        await chat.fill('Can I potter around Denbigh tomorrow rather than fly a 250 km task?');await chat.press('Enter')
        await panel.get_by_text('Mynd northern ridge option',exact=False).wait_for(timeout=90000)
        await panel.get_by_test_id('stAlertContentSuccess').get_by_text('Connected to Gemini (gemini-3-flash-preview)',exact=True).wait_for(timeout=30000)
        answer=await panel.locator('[data-testid="stChatMessage"]').last.inner_text()
        assert 'Browser fixture · Gemini REST' in answer
        assert 'POT — Potton' not in answer and 'No achievable 250' not in answer
        assert 'Denbigh' in answer and 'Five-day' in answer
        await panel.get_by_role('button',name='Load suggested route onto map',exact=True).click()
        await page.get_by_role('tab',name='Task planner',exact=True).click()
        root=page.get_by_role('tabpanel',name='Task planner',exact=True).frame_locator('iframe[title="map_component.duo_map"]').locator('.leaflet-container')
        for _ in range(100):
            await page.wait_for_timeout(300)
            try:
                codes=await root.evaluate('el=>window.map_div._duoTask.points.map(p=>p.code)')
                if codes==['MYN','MNDN','MYN']:break
            except Exception:pass
        assert codes==['MYN','MNDN','MYN'],codes
        assert not await page.locator('[data-testid="stException"]').count()
        result={'provider':'Explicit mock Gemini REST provider','model':'gemini-3-flash-preview','fallback_from':'gemini-3.1-pro-preview','route':codes,'answer':answer}
        # A physical point click hands its coordinate to the bot on leaving the
        # map, without turning each route click into a server round trip.
        await root.scroll_into_view_if_needed()
        await root.evaluate("el=>{window.map_div.setView([53.18355,-3.39718],11,{animate:false});}")
        await page.wait_for_timeout(600)
        point=await root.evaluate("el=>{const m=window.map_div,p=m._duoBga.records.find(p=>p.code==='DEN'),q=m.latLngToContainerPoint([p.lat,p.lon]);return {x:q.x,y:q.y};}")
        box=await root.bounding_box()
        await page.mouse.click(box['x']+point['x'],box['y']+point['y'])
        await page.get_by_role('tab',name='Help & Briefing Bot',exact=True).click()
        await page.wait_for_timeout(2000)
        await chat.fill('weather forecast here for tomorrow');await chat.press('Enter')
        briefing_day=re.search(r'20\d{2}-\d{2}-\d{2}',answer).group()
        await panel.get_by_text('Denbigh · '+briefing_day,exact=True).wait_for(timeout=90000)
        result['clicked_point_briefing']='Denbigh · '+briefing_day
        (out/'results.json').write_text(json.dumps(result,indent=2)+'\n')
        await page.screenshot(path=str(out/'loaded-route.png'),full_page=True)
        print(json.dumps({k:v for k,v in result.items() if k!='answer'}))
        await browser.close()
if __name__=='__main__':asyncio.run(main())
