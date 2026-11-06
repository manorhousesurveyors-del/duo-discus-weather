"""Chromium regression for a fresh live-mode session and asynchronous recovery."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright, expect


async def main():
    out=Path('var/fresh-forecast-browser');out.mkdir(parents=True,exist_ok=True)
    errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1700,'height':1400})
        page.on('pageerror',lambda error:errors.append(str(error)))
        await page.route('https://**/*',lambda route:route.abort())
        await page.add_init_script('''
          window.unavailableFlashes=[];
          document.addEventListener('DOMContentLoaded',()=>{
            new MutationObserver(()=>{
              const text=document.body.innerText;
              for(const value of ['Day classification unavailable','Insufficient forecast coverage','No regional forecast is available'])
                if(text.includes(value))window.unavailableFlashes.push(value);
            }).observe(document.body,{subtree:true,childList:true,characterData:true});
          });
        ''')
        await page.goto('http://127.0.0.1:8507')
        await page.get_by_text('Loading the five-day regional forecast and checking ICON-EU / ECMWF coverage…',exact=True).wait_for(timeout=30000)
        model=page.get_by_role('combobox',name='Forecast model',exact=True)
        await expect(model).to_have_value('DWD ICON-EU · 7 km')
        await expect(page.get_by_text('8/8 local hours covered',exact=False)).to_have_count(5,timeout=30000)
        await expect(page.locator('[data-testid="stMetricValue"]').filter(has_text='★')).to_have_count(5)
        await page.locator('iframe[title="map_component.duo_map"]').last.content_frame.locator('.leaflet-container').wait_for()
        await page.screenshot(path=str(out/'fresh-icon.png'),full_page=True)
        await model.click()
        await page.get_by_role('option',name='Met Office · UKV / seamless',exact=True).click()
        await expect(model).to_have_value('Met Office · UKV / seamless')
        await expect(page.get_by_text('8/8 local hours covered',exact=False)).to_have_count(5,timeout=30000)
        await expect(page.get_by_text('Met Office · UKV / seamless · Forecast supported by',exact=False)).to_be_visible()
        assert not await page.locator('[data-testid="stException"]').count()
        flashes=await page.evaluate('window.unavailableFlashes')
        assert not flashes,flashes
        assert not errors,errors
        result={'default':'icon_eu','days':5,'covered_hours':[8]*5,'pending_state_seen':True,
                'explicit_ukmo_supported':True,'unavailable_flashes':flashes,'errors':errors}
        (out/'results.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
        await browser.close()


if __name__=='__main__':asyncio.run(main())
