"""Chromium cloud-family image and timing checks, without any external network."""
import asyncio
import base64
import json
from pathlib import Path
import subprocess
from playwright.async_api import async_playwright
from verify_thermal_probe import payloads

ROOT=Path(__file__).resolve().parents[1]


async def main():
    payload=payloads()['dry']
    scenes=json.loads(subprocess.run(['node','-e',"console.log(JSON.stringify(require('./tests/cloud_scenes.cjs').scenes()))"],
                                    cwd=ROOT,capture_output=True,text=True,check=True).stdout)
    out=ROOT/'var/cloud-browser';out.mkdir(parents=True,exist_ok=True)
    errors=[];results={}
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1700,'height':1400})
        await page.route('https://**/*',lambda route:route.abort())
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.set_content('<div id="map" style="position:relative;width:1680px;height:1400px"></div><div id="scenes"></div>')
        await page.add_script_tag(content=(ROOT/'assets/point_forecast_meteogram.js').read_text())
        await page.evaluate('''payload=>{
          window.L={DomEvent:{disableClickPropagation(){},disableScrollPropagation(){}}};
          const map={getContainer:()=>document.getElementById('map'),on(){},off(){},hasLayer:()=>false};window.map_div=map;
          installDuoPointProbe(map,payload);map._duoProbe.show({lat:payload.stations[0].lat,lon:payload.stations[0].lon,terrain:100,name:'Synthetic cloud graphics validation'});
        }''',payload)
        assert 'Wing loading: 34 kg/m²' in await page.locator('.duo-point-header').inner_text()
        assert ' · dry · ' not in await page.locator('.duo-point-header').inner_text()
        await page.locator('[data-moisture-profile] summary').click()
        await page.locator('[data-profile-table] tbody tr').first.wait_for()
        assert await page.locator('[data-profile-table] tbody tr').count()>5
        await page.locator('[data-profile-hour]').select_option('18')
        assert 'RH ice %' in await page.locator('[data-profile-table]').inner_text()
        for name,scene in scenes.items():
            result=await page.evaluate('''({name,scene})=>{
              const p=window.map_div._duoProbe,r=structuredClone(p.last);r.rows=r.rows.map(row=>({...row,...scene}));
              const wrap=document.createElement('section');wrap.id='scene-'+name;wrap.style='height:426px;width:1605px;overflow:hidden;background:white';
              const label=document.createElement('h3');label.textContent='Synthetic · '+name;label.style='font:18px Arial;margin:10px';wrap.appendChild(label);
              const canvas=document.createElement('canvas');canvas.style='width:1605px;height:1046px';wrap.appendChild(canvas);document.getElementById('scenes').appendChild(wrap);
              const start=performance.now(),details=p.render(canvas,r),ms=performance.now()-start;
              const pixels=canvas.getContext('2d').getImageData(165,66,1440,310).data;let white=0,dark=0,silver=0,iceLight=0;
              for(let i=0;i<pixels.length;i+=4){const [red,g,b]=pixels.slice(i,i+3);
                if(red>230&&g>230&&b>230)white++;if(red<130&&g<140&&b<160)dark++;if(red>125&&red<225&&Math.abs(red-g)<14&&Math.abs(g-b)<20)silver++;
                if(i<1440*70*4&&red>180&&g>205&&b>225)iceLight++;
              }
              return {ms,details,white,dark,silver,iceLight,png:canvas.toDataURL('image/png')};
            }''',{'name':name,'scene':scene})
            (out/(name+'-full.png')).write_bytes(base64.b64decode(result.pop('png').split(',')[1]))
            await page.locator('#scene-'+name).screenshot(path=str(out/(name+'.png')))
            assert result['ms']<100,(name,result['ms'])
            types={f['type'] for f in result['details']['formations']}
            expected={'humilis':'Cu humilis','mediocris':'Cu mediocris','congestus':'TCu congestus','spread':'Sc / Cu-stratus','storm':'Cb potential','stratus':'Ns','cirrus':'Cs'}
            if name in expected:assert expected[name] in types,(name,types)
            else:assert not types,(name,types)
            if name in ('humilis','mediocris','congestus'):
                assert len(result['details']['clouds'])==24
                assert all(c['base']==scene['lcl'] and c['baseY']>c['topY'] for c in result['details']['clouds'])
            results[name]=result
        assert results['storm']['dark']>results['humilis']['dark']
        assert results['cirrus']['iceLight']>results['clear']['iceLight']
        assert results['stratus']['silver']>results['clear']['silver']
        assert not errors,errors
        (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps({name:{'ms':r['ms'],'types':sorted({f['type'] for f in r['details']['formations']}),'white':r['white'],'dark':r['dark']} for name,r in results.items()}))
        await browser.close()


if __name__=='__main__':asyncio.run(main())
