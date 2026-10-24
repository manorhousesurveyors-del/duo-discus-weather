"""Real browser regression against a recorded Streamlit app on port 8504.
No external requests are allowed; performance is measured on actual canvas work.
"""
import asyncio,base64,json,time,statistics
from pathlib import Path
from playwright.async_api import async_playwright,expect

async def main():
    out=Path('var/point-probe-browser');out.mkdir(exist_ok=True)
    results={'errors':[],'clicks':[],'pages':{}};sent=[];external=[];received=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1700,'height':1400},device_scale_factor=1)
        await page.route('https://**/*',lambda route:(external.append(route.request.url),route.abort())[1])
        page.on('pageerror',lambda error:results['errors'].append(str(error)))
        page.on('websocket',lambda ws:(ws.on('framesent',lambda frame:sent.append(frame)),ws.on('framereceived',lambda _:received.append(time.perf_counter()))))
        await page.goto('http://127.0.0.1:8504')
        nav=page.locator('.st-key-active_page')
        iframe=page.locator('iframe[title="map_component.duo_map"]').last
        frame=lambda:iframe.content_frame
        root=lambda:frame().locator('.leaflet-container')
        toggle=lambda:page.locator('.st-key-point_probe_enabled')
        async def idle():
            await expect(page.get_by_test_id('stApp')).not_to_have_attribute('data-test-script-state','running',timeout=90000)
            # Include component acknowledgements: script-state can briefly be
            # idle before their fragment response reaches the browser.
            for _ in range(100):
                await page.wait_for_timeout(100)
                if (not received or time.perf_counter()-received[-1]>.3) and await page.get_by_test_id('stApp').get_attribute('data-test-script-state')!='running':return
            raise AssertionError('Streamlit transport did not settle')
        async def on():
            await toggle().get_by_text('Enable Point Forecast Probe',exact=True).click()
            await frame().locator('.duo-probe-prompt').wait_for(timeout=60000)
            await idle()
            assert await root().evaluate('el=>!!window.map_div._duoProbe?.active')
            assert await frame().locator('.duo-point-panel').count()==0
        async def at_point(lat,lon):
            await iframe.scroll_into_view_if_needed();box=await root().bounding_box()
            xy=await root().evaluate('(el,p)=>{const q=window.map_div.latLngToContainerPoint(p);return [q.x,q.y];}',[lat,lon])
            await page.mouse.click(box['x']+xy[0],box['y']+xy[1])
        await root().wait_for(timeout=90000);await idle()
        assert not await toggle().locator('input').is_checked()
        assert await root().evaluate("el=>!window.map_div._duoProbe&&!document.querySelector('.duo-point-panel')&&!document.querySelector('.duo-probe-prompt')")
        # Load the BGA catalog with ordinary controls before probing it.
        await page.get_by_text('More layers and click settings',exact=True).click()
        layers=page.get_by_role('combobox',name='Map layers',exact=True)
        await layers.focus();await layers.press('ArrowDown');await page.wait_for_timeout(50)
        for _ in range(8):
            active=await layers.evaluate("el=>document.getElementById(el.getAttribute('aria-activedescendant'))?.textContent")
            if active=='BGA turnpoints':break
            await layers.press('ArrowDown');await page.wait_for_timeout(50)
        assert active=='BGA turnpoints',active
        await layers.press('Enter')
        await expect(page.locator('.st-key-map_layers').get_by_text('BGA turnpoints',exact=True)).to_be_visible()
        # Escape is Streamlit's stop-running shortcut. Wait before dismissing
        # the multiselect so the test cannot cancel the selected map update.
        await idle();await page.keyboard.press('Escape')
        for _ in range(100):
            if await root().evaluate('el=>!!window.map_div?._duoBga'):break
            await page.wait_for_timeout(100)
        else:
            await page.screenshot(path=str(out/'bga-setup-failure.png'),full_page=True)
            raise AssertionError('BGA selection did not reach map; '+await page.locator('.st-key-visual_mode').inner_text())
        # Normal airfield inspection works while the probe is off.
        site=await root().evaluate("el=>window.map_div._duoAirfields.records.find(p=>/Long Mynd/i.test(p.label))")
        await root().evaluate('(el,p)=>window.map_div.setView([p.lat,p.lon],14,{animate:false})',site)
        await page.wait_for_timeout(100);await at_point(site['lat'],site['lon'])
        await frame().locator('.duo-airfield-popup').wait_for()
        assert await frame().locator('.duo-point-panel').count()==0
        results['off_airfield_popup']=True
        await on()
        await root().evaluate('el=>window.map_div.setView([52.60,-2.7],11,{animate:false})')
        await page.wait_for_timeout(100)
        for kind,point in [('coordinate',{'lat':52.60123,'lon':-2.70123}),('airfield',site)]:
            await root().evaluate('(el,p)=>window.map_div.setView([p.lat,p.lon],11,{animate:false})',point)
            await page.wait_for_timeout(100);before=len(sent);network=len(external)
            await at_point(point['lat'],point['lon'])
            await frame().locator('.duo-point-panel').wait_for();await page.wait_for_timeout(100)
            result=await root().evaluate('''el=>{const p=window.map_div._duoProbe,r=p.last.rows;return {ms:p.lastRenderMs,paint_ms:p.lastPaintMs,point:p.last.point,rows:r.length,lcl:r[0].lcl,temperature:r[0].temperature,dewpoint:r[0].dewpoint,terrain:p.last.terrain,ceiling:r[0].ceiling,top:r[0].top};}''')
            assert result['rows']==24,result
            assert result['ms']<100 and result['paint_ms']<100,result
            if kind=='airfield':
                assert result['point']['name']==(site.get('name') or site['label']),result
                assert result['point']['lat']==site['lat'] and result['point']['lon']==site['lon'],result
            if result['lcl'] is not None:
                assert abs(result['lcl']-(result['terrain']+400*max(0,result['temperature']-result['dewpoint'])))<.001
            assert len(sent)==before,'Probe click caused a Streamlit event'
            assert len(external)==network,'Probe click started an external request'
            results['clicks'].append({'kind':kind,**result})
            if kind=='coordinate':await frame().locator('[data-probe-another]').click()
        # Inspect the scale, wind symbols and missing coverage in the real JS engine.
        checks=await root().evaluate("""el=>{const p=window.map_div._duoProbe;return {
          parts:p.barbParts(65),missing:p.profileAt({lat:0,lon:0}).rows.every(r=>r.ceiling===null&&r.climb===null),
          scale:[0,1000,3000,5000,7000,16000,38000].map(ft=>[ft,p.altitude(p.fraction(ft))])};}""")
        assert checks['parts']=={'flags':1,'full':1,'half':True}
        assert checks['missing']
        assert all(abs(a-b)<.001 for a,b in checks['scale'])
        results['math_checks']=checks
        # The requested legend stays fixed while the two-day hourly grid scrolls.
        label_box=await frame().locator('.duo-point-labels').bounding_box()
        assert abs(label_box['width']-165)<.1,label_box
        await frame().locator('.duo-point-graphic').evaluate('el=>el.scrollLeft=500')
        after_scroll=await frame().locator('.duo-point-labels').bounding_box()
        assert abs(after_scroll['x']-label_box['x'])<1,(label_box,after_scroll)
        await frame().locator('.duo-point-graphic').evaluate('el=>el.scrollLeft=0')
        # Exercise actual renderer branches with explicitly synthetic profiles.
        visual=await root().evaluate("""el=>{
          const p=window.map_div._duoProbe,base=structuredClone(p.last),scenes={};
          const draw=(name,top,lcl,cover,soar=true)=>{
            const result=structuredClone(base),canvas=document.createElement('canvas');
            result.rows.forEach(r=>{r.top=top;r.lcl=lcl;r.cloud_low=cover;r.soar=soar;r.climb=soar?3:null;r.cu=top-lcl;});
            const started=performance.now(),details=p.render(canvas,result);
            scenes[name]={ms:performance.now()-started,details,png:canvas.toDataURL('image/png')};
          };
          draw('shallow',5600,5000,35);draw('towering',14000,3500,50);
          draw('overcast',9000,3000,98,false);draw('blue',4500,7000,0);
          draw('missing',null,null,null,false);
          const descriptor=Object.getOwnPropertyDescriptor(window,'devicePixelRatio');
          Object.defineProperty(window,'devicePixelRatio',{configurable:true,value:2});
          draw('retina',14000,3500,50);
          if(descriptor)Object.defineProperty(window,'devicePixelRatio',descriptor);else delete window.devicePixelRatio;
          const times=base.rows.slice(0,2).map(r=>r.iso),obs=times.map((time,i)=>({station:'TEST',name:'Synthetic observation',lat:base.point.lat,lon:base.point.lon,time,temperature:10+i,stale:false}));
          const observed=structuredClone(base);observed.observation=p.nearestObservation(base.point,obs);
          const observedDetails=p.render(document.createElement('canvas'),observed);
          return {scenes,observedDetails,emptyObs:p.nearestObservation(base.point,[]),
            sun:p.solarTimes({lat:51.4779,lon:0},'2026-06-21'),polar:p.solarTimes({lat:85,lon:0},'2026-06-21'),cloudWithoutNetLift:p.cloudColumn({top:6000,lcl:5000,cloud_low:0,soar:false}),
            baseline:base.rows.filter(r=>r.soar===false).every(r=>r.displayMsl===base.terrain&&r.agl===0),details:p.lastRenderDetails};
        }""")
        assert visual['baseline'] and visual['emptyObs'] is None
        assert visual['cloudWithoutNetLift']['depth']==1000
        assert visual['observedDetails']['observations']==2
        assert visual['polar']['polar']=='Midnight sun'
        to_minutes=lambda s:int(s[:2])*60+int(s[3:])
        assert abs(to_minutes(visual['sun']['rise'])-(4*60+44))<=5,visual['sun']
        assert abs(to_minutes(visual['sun']['set'])-(21*60+21))<=5,visual['sun']
        assert visual['details']['legendWidth']==165 and visual['details']['barbs']>0
        assert visual['details']['borderedCells']==216,visual['details']
        for name,scene in visual['scenes'].items():
            (out/(name+'.png')).write_bytes(base64.b64decode(scene.pop('png').split(',',1)[1]))
            assert scene['ms']<100,(name,scene['ms'])
            clouds=scene['details']['clouds']
            if name in ('blue','missing'):assert not clouds
            else:
                assert len(clouds)==24
                assert all(c['base']<c['top'] and c['baseY']>c['topY'] for c in clouds)
                if name=='shallow':assert all(1<=c['layers']<=2 for c in clouds)
                if name=='towering':assert all(c['layers']>=5 for c in clouds)
                if name=='overcast':assert all(c['overcast'] for c in clouds)
        results['visual_checks']=visual
        # Export the actual HiDPI canvas for full-height visual review.
        png=await frame().locator('.duo-point-canvas').evaluate("el=>el.toDataURL('image/png')")
        (out/'full-chart.png').write_bytes(base64.b64decode(png.split(',',1)[1]))
        await frame().locator('.duo-point-panel').screenshot(path=str(out/'meteogram.png'))
        # Closing must also reset the Streamlit switch, not just remove the SVG.
        await frame().locator('[data-probe-close]').click();await idle()
        await expect(toggle().locator('input')).not_to_be_checked(timeout=30000)
        await expect(frame().locator('.duo-point-panel')).to_have_count(0)
        assert await root().evaluate('el=>!window.map_div._duoProbe')
        # Enable BGA layer; native canvas marker click must inspect, never add.
        await on()
        waypoint=await root().evaluate('''el=>{const m=window.map_div;
          if(!m._duoBga){return null;}m._duoBga.addTo(m);return m._duoBga.records.find(p=>p.code==='MYN');}''')
        assert waypoint is not None,'The enabled BGA layer must be available to the probe'
        await root().evaluate('(el,p)=>window.map_div.setView([p.lat,p.lon],11,{animate:false})',waypoint)
        await page.wait_for_timeout(100);before=len(sent);await at_point(waypoint['lat'],waypoint['lon'])
        await frame().locator('.duo-point-panel').wait_for();await page.wait_for_timeout(100)
        assert len(sent)==before
        assert await root().evaluate('(el,name)=>window.map_div._duoProbe.last.point.name===name',waypoint['name'])
        results['bga_click']=True
        await toggle().get_by_text('Enable Point Forecast Probe',exact=True).click();await idle()
        await expect(frame().locator('.duo-probe-prompt')).to_have_count(0)
        assert await root().evaluate('el=>!window.map_div._duoProbe')
        # Leaving Weather while enabled must reset it, not revive it on return.
        await on()
        for name,marker in [('Task planner','[data-action="clear"]'),('Wave watch','[data-wave-level="0"]'),('Live OGN Glider Radar','[data-ogn="demo"]'),('Help Guide',None),('Help & Briefing Bot',None),('Data quality',None)]:
            start=time.perf_counter();await nav.get_by_text(name,exact=True).click();await idle()
            if marker:
                await frame().locator(marker).wait_for(timeout=90000)
                assert await root().evaluate("el=>!window.map_div._duoProbe&&!document.querySelector('.duo-point-panel')&&!document.querySelector('.duo-probe-prompt')")
            else:await expect(page.locator('iframe[title="map_component.duo_map"]')).to_have_count(0)
            assert await toggle().count()==0
            results['pages'][name]=round((time.perf_counter()-start)*1000,1)
        await nav.get_by_text('Weather map',exact=True).click();await idle()
        await expect(toggle().locator('input')).not_to_be_checked()
        assert await root().evaluate('el=>!window.map_div._duoProbe')
        results['warm_pages']={}
        await page.evaluate("""() => {
          document.addEventListener('click',e=>{
            const item=e.target.closest('.st-key-active_page label');
            if(item){window._navAt=Date.now();window._navName=item.innerText.trim();window._textReadyAt=null;}
          },true);
          new MutationObserver(()=>{
            const heading=window._navName==='Data quality'?'Forecast quality':window._navName;
            if(window._navAt&&!window._textReadyAt&&[...document.querySelectorAll('h1,h2,h3')].some(h=>h.innerText.trim()===heading))window._textReadyAt=Date.now();
          }).observe(document.body,{subtree:true,childList:true});
        }""")
        # Three real navigation samples separate normal timing from scheduler
        # jitter. Retain every sample; enforce the warm median, not the fastest.
        for trial in range(3):
            for name,marker in [('Task planner','[data-action="clear"]'),('Wave watch','[data-wave-level="0"]'),('Live OGN Glider Radar','[data-ogn="demo"]'),('Help Guide',None),('Help & Briefing Bot',None),('Data quality',None),('Weather map','[data-clear-task]')]:
                start=time.perf_counter();await nav.get_by_text(name,exact=True).click()
                if marker:await frame().locator(marker).wait_for(timeout=30000)
                else:
                    heading={'Data quality':'Forecast quality'}.get(name,name)
                    await page.get_by_role('heading',name=heading,exact=True).wait_for()
                    await expect(page.locator('iframe[title="map_component.duo_map"]')).to_have_count(0)
                automation_ms=round((time.perf_counter()-start)*1000,1)
                timing=await root().evaluate('el=>window._duoRenderTiming') if marker else None
                nav_at,text_at=await page.evaluate('[window._navAt,window._textReadyAt]')
                elapsed=(timing['readyAt'] if timing else text_at)-nav_at
                results['warm_pages'].setdefault(name,[]).append({'click_to_ready_ms':elapsed,'automation_ms':automation_ms,'map':timing})
                await idle()
        results['warm_median_ms']={name:statistics.median(s['click_to_ready_ms'] for s in samples) for name,samples in results['warm_pages'].items()}
        print('Warm navigation samples:',results['warm_pages'],flush=True)
        assert all(ms<500 for ms in results['warm_median_ms'].values()),results['warm_median_ms']
        # Losing the tab-memory cache must request and restore the document.
        await nav.get_by_text('Help Guide',exact=True).click();await idle()
        await page.evaluate('window._duoDecodedMaps.clear()')
        await nav.get_by_text('Weather map',exact=True).click()
        await frame().locator('[data-clear-task]').wait_for(timeout=30000);await idle()
        assert await root().evaluate('el=>!!window.map_div && !window.map_div._duoProbe')
        results['cache_miss_recovery']=True
        assert not results['errors'],results['errors']
        assert not await page.locator('[data-testid="stException"]').count()
        (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps(results));await browser.close()

if __name__=='__main__':asyncio.run(main())
