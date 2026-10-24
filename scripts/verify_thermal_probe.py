"""Render the real probe with labelled synthetic 25 October physics cases.

No server or public weather requests. The payload is built by the production
Python physics; Chromium executes the production renderer and inspects pixels.
"""
import asyncio,base64,json,subprocess
from pathlib import Path
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]


def payloads():
    code='''
import json
from dataclasses import replace
from tests.test_thermal_revision import moderate_forecast
from point_forecast_meteogram import prepare_bundle
from config import FORECAST_MODELS
f=moderate_forecast(shortwave_radiation=[325. if 11<=i%24<=16 else 0. for i in range(145)])
uk=replace(f,model=FORECAST_MODELS['ukmo'].api_name)
fallback=replace(f,hourly={**f.hourly,'temperature_2m':(9.,)*145})
missing=replace(uk,hourly={**uk.hourly,'temperature_2m':tuple(8. if i<24 else None for i in range(145))})
print(json.dumps({name:prepare_bundle((name,),f.times[0].date(),loading,_forecasts=forecasts)
 for name,forecasts,loading in [('dry',(f,),'dry'),('ballasted',(f,),'ballasted'),('paired',(missing,fallback),'dry')]}))
'''
    r=subprocess.run([str(ROOT/'.venv/bin/python'),'-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
    return json.loads(r.stdout)


async def main():
    bundles=payloads();out=ROOT/'var/thermal-probe-browser';out.mkdir(parents=True,exist_ok=True)
    results={};errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1700,'height':1400})
        await page.route('https://**/*',lambda route:route.abort())
        page.on('pageerror',lambda error:errors.append(str(error)))
        await page.set_content('<div id="map" style="position:relative;width:1680px;height:1400px"></div>')
        await page.add_script_tag(content=(ROOT/'assets/point_forecast_meteogram.js').read_text())
        for name,payload in bundles.items():
            result=await page.evaluate('''({payload,name})=>{
              window.map_div?._duoProbe?.disable(false);
              window.L={DomEvent:{disableClickPropagation(){},disableScrollPropagation(){}}};
              const map={getContainer:()=>document.getElementById('map'),on(){},off(){},hasLayer:()=>false};
              window.map_div=map;installDuoPointProbe(map,payload);
              const p=map._duoProbe;p.show({lat:payload.stations[0].lat,lon:payload.stations[0].lon,terrain:1460,name:'Synthetic Midland Gliding Club · Sun 25 Oct'});
              const rows=p.last.rows,canvas=document.querySelector('.duo-point-canvas'),c=canvas.getContext('2d'),pixels=c.getImageData(165,66,1440,310).data;
              let blue=0,grey=0,white=0;
              for(let i=0;i<pixels.length;i+=4){const [r,g,b]=pixels.slice(i,i+3);
                if(b>145&&b>r*1.4&&g<180)blue++;
                if(r>110&&r<180&&Math.abs(r-g)<20&&Math.abs(g-b)<25)grey++;
                if(r>230&&g>230&&b>230)white++;
              }
              return {ms:p.lastRenderMs,rows:rows.map(r=>({day:r.day,label:r.label,climb:r.climb,agl:r.agl,msl:r.displayMsl,core:r.core,sink:r.sink,solar:r.solar,sources:r.sources,temperature:r.temperature})),stats:p.lastRenderDetails,
               pixels:{blue,grey,white},nearCloud:p.cloudColumn({top:4950,lcl:5100,cloud_low:30}),dryGap:p.cloudColumn({top:4900,lcl:5200,cloud_low:100}),exactCloud:p.cloudColumn({top:5100,lcl:5100,cloud_low:0}),png:canvas.toDataURL('image/png')};
            }''',{'payload':payload,'name':name})
            (out/(name+'.png')).write_bytes(base64.b64decode(result.pop('png').split(',')[1]))
            assert result['ms']<100,result['ms']
            assert all(r['climb']>=0 for r in result['rows'])
            for r in result['rows']:
                assert abs(r['climb']-max(0,round(r['core']-r['sink'],1)))<.001,r
                assert abs(r['msl']-1460-r['agl'])<.001,r
                if r['solar']==0:assert r['climb']==0 and r['agl']==0,r
            if name=='dry':
                active=[r for r in result['rows'] if r['solar']==325]
                assert active and all(1.5<=r['climb']<=2.3 and r['agl']>1000 for r in active)
            if name=='paired':
                assert all('ukmo_seamless' in r['sources'] and r['temperature']==8 for r in result['rows'][:12])
                assert all('ecmwf_ifs025' in r['sources'] and r['temperature']==9 for r in result['rows'][12:])
            assert result['stats']['contours']>0 and result['stats']['clouds']
            assert all(n>0 for n in result['stats']['cloudPixels'].values())
            assert all(n>100 for n in result['pixels'].values()),result['pixels']
            assert result['nearCloud']['nearBase'] and result['exactCloud']['nearBase']
            assert result['dryGap'] is None
            results[name]=result
        assert not errors,errors
        (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps({k:{'ms':v['ms'],'pixels':v['pixels'],'cloud_pixels':v['stats']['cloudPixels']} for k,v in results.items()}))
        await browser.close()

if __name__=='__main__':asyncio.run(main())
