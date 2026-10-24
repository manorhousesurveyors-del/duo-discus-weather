/* Optional Weather-only probe. This file is absent from every disabled map. */
function installDuoPointProbe(map, data) {
 'use strict';
 const container=map.getContainer(), finite=Number.isFinite;
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const mix=(a,b,t)=>finite(a)&&finite(b)?a+(b-a)*t:null;
 const fmt=(v,n=0)=>finite(v)?v.toLocaleString('en-GB',{maximumFractionDigits:n,minimumFractionDigits:n}):'—';
 const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
 const heading=(u,v)=>finite(u)&&finite(v)&&Math.hypot(u,v)>1e-6?(Math.atan2(-u,-v)*180/Math.PI+360)%360:null;
 const speed=(u,v)=>finite(u)&&finite(v)?Math.hypot(u,v)*1.943844:null;
 const distance=(a,b)=>{const rad=Math.PI/180,p=a.lat*rad,q=b.lat*rad;return 12742.0176*Math.asin(Math.sqrt(Math.min(1,Math.sin((q-p)/2)**2+Math.cos(p)*Math.cos(q)*Math.sin((b.lon-a.lon)*rad/2)**2)));};
 let panel=null, active=true, last=null, pointerStart=null, moved=false;
 const style=document.createElement('style');
 style.textContent=`.duo-probe-active,.duo-probe-active .leaflet-interactive{cursor:crosshair!important}
 .duo-point-panel{position:absolute;z-index:1100;left:10px;right:10px;bottom:10px;max-height:88%;background:#fdfbf7;color:#1e303e;border:1px solid #afc4cc;border-radius:14px;box-shadow:0 8px 35px #12232c55;font:14px/1.5 Arial,sans-serif;cursor:auto;overflow:auto}
 .duo-point-header{position:sticky;top:0;z-index:2;background:#122e40;color:#f5fbfd;padding:12px 16px;display:flex;gap:12px;justify-content:space-between;align-items:start}
 .duo-point-header h2{font-size:17px;margin:0}.duo-point-header small{display:block;color:#c6dce2}
 .duo-point-panel button{background:#f5e6bd;color:#172d3b;border:0;border-radius:8px;padding:10px 14px;font:bold 14px Arial;cursor:pointer;white-space:nowrap}
 .duo-point-summary{display:flex;gap:16px;flex-wrap:wrap;padding:10px 16px;background:#edf4f2;border-bottom:1px solid #d3e1df}
 .duo-point-summary b{display:block;color:#11665f}.duo-point-notes{padding:8px 16px;font-size:12px;color:#425563}
 .duo-point-graphic{overflow:auto;padding:8px 10px}.duo-point-graphic canvas{display:block;width:1600px;height:850px;max-width:none}
 .duo-point-panel details{padding:8px 16px}.duo-point-panel summary{cursor:pointer}.duo-point-panel table{border-collapse:collapse;font-size:12px}.duo-point-panel th,.duo-point-panel td{padding:5px 10px;border-bottom:1px solid #d3e1df;white-space:nowrap}
 .duo-probe-prompt{position:absolute;left:50%;top:12px;transform:translateX(-50%);z-index:900;max-width:65%;padding:8px 14px;background:#173748ed;color:#effcff;border-radius:20px;font:13px/1.4 Arial;pointer-events:none;text-align:center}`;
 container.appendChild(style);container.classList.add('duo-probe-active');
 const prompt=document.createElement('div');prompt.className='duo-probe-prompt';prompt.textContent='Point Forecast Probe active · click a location to inspect';container.appendChild(prompt);

 // Suspend marker hover requests only while probing; normal listeners return
 // on close. Newly enabled canvas layers follow the same rule.
 const suspended=new Set();
 function suspend(layer){if(!layer||typeof layer.hover!=='function'||suspended.has(layer))return;
  map.off('mousemove',layer.hover,layer);layer.options.onLeave?.();if(layer.tip)map.removeLayer(layer.tip);suspended.add(layer);
 }
 [map._duoBga,map._duoAirfields].filter(layer=>layer&&map.hasLayer(layer)).forEach(suspend);
 const added=event=>{if(event.layer===map._duoBga||event.layer===map._duoAirfields){suspended.delete(event.layer);suspend(event.layer);}};
 map.on('layeradd',added);
 function weighted(values,weights) {return values.every(finite)?values.reduce((a,v,i)=>a+v*weights[i],0):null;}
 function vertical(levels,height,field) {
  for(let i=0;i<levels.length;i++){
   const a=levels[i];if(a[0]===height)return a[field];
   const b=levels[i+1];if(b&&finite(a[0])&&finite(b[0])&&height>=a[0]&&height<=b[0]&&b[0]>a[0])return mix(a[field],b[field],(height-a[0])/(b[0]-a[0]));
  }return null;
 }
 function profileAt(point) {
  let nearest=data.stations.map(station=>({station,d:distance(point,station)})).filter(s=>s.d<=data.max_distance_km).sort((a,b)=>a.d-b.d).slice(0,4);
  if(nearest[0]?.d<.05)nearest=nearest.slice(0,1);
  const raw=nearest.map(s=>1/Math.max(.05,s.d)**2),sum=raw.reduce((a,b)=>a+b,0),weights=raw.map(w=>w/sum);
  const terrain=finite(point.terrain)?point.terrain:nearest.length?weighted(nearest.map(s=>s.station.terrain),weights):null;
  const rows=data.columns.map(column=>{
   const avg=(part,index,field)=>{
    if(!nearest.length)return null;
    const values=nearest.map(({station})=>{
     const [a,b]=column.indices.map(i=>station.hours[i]);
     return part==='s'?mix(a?.s[index],b?.s[index],.5):mix(a?.p[index]?.[field],b?.p[index]?.[field],.5);
    });return weighted(values,weights);
   };
   const row=Object.fromEntries(data.fields.map((name,i)=>[name,avg('s',i)]));
   row.levels=data.levels.map((_,i)=>Array.from({length:4},(_,j)=>avg('p',i,j))).filter(p=>finite(p[0])&&p[0]>terrain);
   row.lcl=finite(terrain)&&finite(row.temperature)&&finite(row.dewpoint)?terrain+400*Math.max(0,row.temperature-row.dewpoint):null;
   row.top=finite(terrain)&&finite(row.depth)?terrain+Math.max(0,row.depth):null;
   row.cu=finite(row.top)&&finite(row.lcl)?Math.max(0,row.top-row.lcl):null;
   const known=[terrain,row.top,row.lcl,row.climb,row.rain,row.solar].every(finite);
   row.soar=known?row.climb>0&&row.depth>500&&row.rain<1&&row.solar>0:null;
   row.ceiling=row.soar===null?null:row.soar?Math.min(row.top,row.lcl):0;
   row.agl=row.ceiling===null?null:row.soar?Math.max(0,row.ceiling-terrain):0;
   const rh=finite(row.temperature)&&finite(row.dewpoint)?clamp(100*Math.exp(17.625*row.dewpoint/(243.04+row.dewpoint)-17.625*row.temperature/(243.04+row.temperature)),0,100):null;
   row.profile=[[terrain+32.8084,rh,row.u,row.v],...row.levels].filter(p=>finite(p[0])).sort((a,b)=>a[0]-b[0]);
   row.topU=finite(row.top)?vertical(row.profile,row.top,2):null;row.topV=finite(row.top)?vertical(row.profile,row.top,3):null;
   return {...row,...column};
  });
  const dates=[...new Set(data.columns.map(c=>c.day))];
  const windows=dates.map(day=>{const r=rows.filter(r=>r.day===day),segments=[];let start=null,end=null;
   r.forEach((row,i)=>{if(row.soar){if(start===null)start=row.label;end=row.label;}if(start!==null&&(!row.soar||i===r.length-1)){segments.push(start+'–'+end);start=null;}});
   return day+': '+(segments.join(', ')||(r.some(r=>r.soar===null)?'unavailable':'none'));
  });
  return {point,rows,terrain,nearest:nearest.map(s=>({name:s.station.name,d:s.d,retrieved:s.station.retrieved})),windows};
 }

 // Deliberately expand the lowest 7,000 ft; never extrapolate cloud/wind data.
 const ticks=[0,1000,3000,5000,7000,16000,38000],positions=[1,.91,.73,.54,.37,.18,0];
 function fraction(ft){ft=clamp(ft,0,38000);for(let i=0;i<ticks.length-1;i++)if(ft<=ticks[i+1])return mix(positions[i],positions[i+1],(ft-ticks[i])/(ticks[i+1]-ticks[i]));return 0;}
 function altitude(f){for(let i=0;i<positions.length-1;i++)if(f>=positions[i+1])return mix(ticks[i],ticks[i+1],(positions[i]-f)/(positions[i]-positions[i+1]));return 38000;}
 function barbParts(knots){let n=Math.round(Math.max(0,knots)/5)*5;const flags=Math.floor(n/50);n-=flags*50;return {flags,full:Math.floor(n/10),half:n%10>=5};}
 function barb(ctx,x,y,u,v,scale=1){
  const knots=speed(u,v);if(knots===null)return;ctx.save();ctx.translate(x,y);ctx.scale(scale,scale);ctx.strokeStyle='#142b3c';ctx.fillStyle='#142b3c';ctx.lineWidth=1.25;
  if(knots<2.5){ctx.beginPath();ctx.arc(0,0,2.5,0,Math.PI*2);ctx.stroke();ctx.restore();return;}
  ctx.rotate(heading(u,v)*Math.PI/180);ctx.beginPath();ctx.moveTo(0,8);ctx.lineTo(0,-16);ctx.stroke();let h=-16;
  const parts=barbParts(knots);for(let i=0;i<parts.flags;i++){ctx.beginPath();ctx.moveTo(0,h);ctx.lineTo(10,h+4);ctx.lineTo(0,h+8);ctx.fill();h+=9;}
  for(let i=0;i<parts.full;i++){ctx.beginPath();ctx.moveTo(0,h);ctx.lineTo(10,h+5);ctx.stroke();h+=4;}
  if(parts.half){if(!parts.full&&!parts.flags)h+=4;ctx.beginPath();ctx.moveTo(0,h);ctx.lineTo(5,h+2.5);ctx.stroke();}ctx.restore();
 }
 function arrow(ctx,x,y,u,v){if(!finite(u)||!finite(v))return;ctx.save();ctx.translate(x,y);ctx.rotate((heading(u,v)+180)*Math.PI/180);ctx.strokeStyle='#24485b';ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(0,6);ctx.lineTo(0,-6);ctx.moveTo(-3,-2);ctx.lineTo(0,-6);ctx.lineTo(3,-2);ctx.stroke();ctx.restore();}
 function liftColour(kt){const stops=[[.5,[43,82,173]],[1.5,[25,151,161]],[2.5,[49,207,218]],[3.5,[40,177,123]],[5,[236,189,65]]];for(let i=0;i<stops.length-1;i++)if(kt<=stops[i+1][0])return stops[i][1].map((v,j)=>mix(v,stops[i+1][1][j],clamp((kt-stops[i][0])/(stops[i+1][0]-stops[i][0]),0,1)));return stops.at(-1)[1];}
 function draw(canvas,result){
  const ratio=Math.min(2,window.devicePixelRatio||1),W=1600,H=850,left=170,right=22,top=53,gh=305,width=W-left-right,cw=width/24;
  canvas.width=W*ratio;canvas.height=H*ratio;const c=canvas.getContext('2d');c.scale(ratio,ratio);
  const x=i=>left+cw*(i+.5),y=ft=>top+gh*fraction(ft),rows=result.rows;
  c.fillStyle='#fdfbf7';c.fillRect(0,0,W,H);c.font='12px Arial';c.textBaseline='middle';
  const label=(text,xx,yy,colour='#1e3647',align='left')=>{c.fillStyle=colour;c.textAlign=align;c.fillText(text,xx,yy);};
  c.font='bold 14px Arial';label('ALTITUDE · ft AMSL',12,19);c.font='12px Arial';
  for(let day=0;day<2;day++){
   label(rows[day*12].day+' · '+rows[day*12].zone,left+(day*12+6)*cw,14,'#185e69','center');
   for(let i=day*12;i<(day+1)*12;i++)label(rows[i].label,x(i),37,'#213a49','center');
  }
  // Small raster scales smoothly to a HiDPI canvas; draw cost stays bounded.
  const raster=document.createElement('canvas'),rw=576,rh=184;raster.width=rw;raster.height=rh;
  const rc=raster.getContext('2d'),pixels=rc.createImageData(rw,rh),heights=Array.from({length:rh},(_,j)=>altitude(j/(rh-1)));
  const cloudProfiles=rows.map(row=>heights.map(h=>vertical(row.profile,h,1)));
  for(let ix=0;ix<rw;ix++){
   const day=ix<rw/2?0:1,offset=day*12,position=clamp((ix-day*rw/2)/(rw/2)*12-.5,0,11),lo=Math.floor(position)+offset,hi=Math.min(offset+11,lo+1),t=position%1;
   const a=rows[lo],b=rows[hi],ceiling=mix(a.ceiling,b.ceiling,t),climb=mix(a.climb,b.climb,t),colour=liftColour(climb||0);
   for(let j=0;j<rh;j++){
    const h=heights[j],humidity=mix(cloudProfiles[lo][j],cloudProfiles[hi][j],t),idx=(j*rw+ix)*4;let rgb;
    if(h<result.terrain)rgb=[157,164,156];
    else if(humidity===null)rgb=(Math.floor(ix/7)+Math.floor(j/7))%2?[233,237,239]:[243,245,246];
    else {const key=h<7000?'cloud_low':h<20000?'cloud_mid':'cloud_high',cover=mix(a[key],b[key],t);
     const opacity=clamp((humidity-65)/35,0,1),cloud=opacity*(cover===null?1:.35+.65*clamp(cover/100,0,1));
     const shade=clamp((humidity-88)/12,0,1),grey=[246-139*shade,248-132*shade,249-127*shade];
     rgb=[111,180,219].map((v,k)=>mix(v,grey[k],cloud));
     if(finite(ceiling)&&ceiling>0&&h<ceiling&&h>result.terrain&&climb>0){const depth=(h-result.terrain)/Math.max(1,ceiling-result.terrain),plume=.35+.5*Math.sin(Math.PI*depth)*(.6+.4*Math.cos(ix*.24));rgb=rgb.map((v,k)=>mix(v,colour[k],plume));}
    }
    pixels.data[idx]=rgb[0];pixels.data[idx+1]=rgb[1];pixels.data[idx+2]=rgb[2];pixels.data[idx+3]=255;
   }
  }
  rc.putImageData(pixels,0,0);c.imageSmoothingEnabled=true;c.drawImage(raster,left,top,width,gh);
  for(const ft of ticks){c.strokeStyle='#ffffff77';c.lineWidth=1;c.beginPath();c.moveTo(left,y(ft));c.lineTo(W-right,y(ft));c.stroke();label(fmt(ft),left-12,y(ft),'#314857','right');}
  function curve(field,yy,colour,lineWidth=2,dash=[]){c.strokeStyle=colour;c.lineWidth=lineWidth;c.setLineDash(dash);c.beginPath();let open=false;rows.forEach((row,i)=>{if(i===12)open=false;const value=row[field];if(!finite(value)){open=false;return;}const py=yy(value);if(open)c.lineTo(x(i),py);else c.moveTo(x(i),py);open=true;});c.stroke();c.setLineDash([]);}
  c.save();c.beginPath();c.rect(left,top,width,gh);c.clip();
  curve('top',y,'#33434e',1.2,[4,3]);curve('ceiling',v=>v>0?y(v):y(0),'#172b39',4);curve('lcl',y,'#e5688c',1.7);
  rows.forEach((row,i)=>{
   if(row.cu>0&&row.soar){const base=y(row.lcl),cloudTop=y(row.top);c.fillStyle='#eef1f6a8';c.fillRect(x(i)-14,cloudTop,28,base-cloudTop);
    c.fillStyle='#f9fbfc';c.strokeStyle='#8296a4';c.lineWidth=.7;c.beginPath();c.moveTo(x(i)-15,base);c.bezierCurveTo(x(i)-22,base-9,x(i)-12,base-16,x(i)-7,base-9);c.bezierCurveTo(x(i)-7,base-24,x(i)+9,base-24,x(i)+10,base-10);c.bezierCurveTo(x(i)+20,base-15,x(i)+24,base,x(i)+15,base);c.closePath();c.fill();c.stroke();
   }
   row.levels.forEach(p=>{if(p[0]>=result.terrain&&p[0]<=38000)barb(c,x(i),y(p[0]),p[2],p[3],.72);});
  });c.restore();
  label('Solid black: working ceiling · dotted: mixing top · rose: LCL · hatching: missing upper-air data',left,377,'#415a6b');
  label('Lift: cobalt 0.5 kt → teal 1.5 → cyan 2.5 → emerald 3.5 → gold 5+ kt',left,396,'#146b6a');
  const strip=(name,yy,value,options={})=>{label(name,12,yy+14);rows.forEach((row,i)=>{c.fillStyle=options.hazard?options.hazard(row):row.soar?'#dceee5':i%2?'#edf1f3':'#f5f7f8';c.fillRect(left+i*cw,yy,cw-1,28);label(value(row),x(i),yy+14,'#1c454d','center');});};
  strip('Working ceiling · ft AMSL',414,r=>fmt(r.ceiling));strip('Working ceiling · ft AGL',445,r=>fmt(r.agl));
  strip('Net thermal climb · kt',476,r=>fmt(r.climb,1));
  label('Solar / sensible heat',12,522);
  rows.forEach((row,i)=>{c.fillStyle='#e7dfc9';c.fillRect(left+i*cw,510,cw-1,26);if(finite(row.solar)){c.fillStyle='#e5b34f';c.fillRect(left+i*cw,536-clamp(row.solar/1200,0,1)*26,cw-1,clamp(row.solar/1200,0,1)*26);}label(fmt(row.solar)+' / '+fmt(row.heat),x(i),523,'#2a4451','center');});
  label('W/m²',12,538,'#667984');
  const temps=rows.flatMap(r=>[r.temperature,r.dewpoint]).filter(finite),minimum=temps.length?Math.floor(Math.min(...temps)/5)*5:0,maximum=Math.max(minimum+10,temps.length?Math.ceil(Math.max(...temps)/5)*5:30),ty=v=>599-(v-minimum)/(maximum-minimum)*48;
  label('Temperature / dewpoint · °C',12,574);label(fmt(maximum)+'°',left-10,551,'#70818b','right');label(fmt(minimum)+'°',left-10,599,'#70818b','right');
  curve('temperature',ty,'#e57461',2.6);curve('dewpoint',ty,'#39aace',2.6);
  c.font='11px Arial';rows.forEach((r,i)=>label(fmt(r.temperature,1)+' / '+fmt(r.dewpoint,1),x(i),615,'#425d6c','center'));c.font='12px Arial';
  label('Surface wind · 10 m AGL',12,649);label('kt · g = gust kt / from °T',12,666,'#667984');
  rows.forEach((r,i)=>{c.fillStyle=finite(r.gust)&&r.gust>=30?'#f5c9c2':finite(r.gust)&&r.gust>=20?'#f5e4b9':'#e8f1f4';c.fillRect(left+i*cw,635,cw-1,44);arrow(c,x(i),642,r.u,r.v);c.font='11px Arial';label(fmt(speed(r.u,r.v))+' · g'+fmt(r.gust),x(i),654,'#274d5c','center');label(fmt(heading(r.u,r.v))+'°T',x(i),670,'#274d5c','center');c.font='12px Arial';});
  label('Soaring layer top wind',12,700);label('kt / from °T',12,718,'#667984');
  rows.forEach((r,i)=>{arrow(c,x(i),695,r.topU,r.topV);label(fmt(speed(r.topU,r.topV))+' / '+fmt(heading(r.topU,r.topV)),x(i),716,'#274d5c','center');});
  label('Sea-level pressure',12,757);label('hPa',12,777,'#667984');const py=v=>799-(clamp(v,990,1030)-990)/40*60;
  for(const hpa of [990,1010,1030]){c.strokeStyle='#cddce1';c.lineWidth=.6;c.beginPath();c.moveTo(left,py(hpa));c.lineTo(W-right,py(hpa));c.stroke();label(String(hpa),left-10,py(hpa),'#607984','right');}
  curve('mslp',py,'#576aa2',2.1);rows.forEach((r,i)=>{
   const outside=finite(r.mslp)&&(r.mslp<990||r.mslp>1030);
   if(outside){const yy=py(r.mslp),dir=r.mslp<990?1:-1;c.fillStyle='#aa594e';c.beginPath();c.moveTo(x(i)-4,yy);c.lineTo(x(i)+4,yy);c.lineTo(x(i),yy+dir*5);c.fill();}
   label(fmt(r.mslp,1),x(i),816,outside?'#aa594e':'#51638a','center');
  });
  c.strokeStyle='#637f91';c.lineWidth=2;c.beginPath();c.moveTo(left+12*cw,0);c.lineTo(left+12*cw,830);c.stroke();
  label('Clouds: RH / model cover proxy · wind barbs: half 5 kt, full 10 kt, pennant 50 kt · pressure arrows mark off-scale values',left,841,'#586e7b');
 }
 function nearestFeature(point){const records=[...(map._duoBga?.records||[]),...(map._duoAirfields?.records||[])];let best=null,km=Infinity;for(const row of records){const d=distance(point,row);if(d<km){best=row;km=d;}}return best&&km<30?{name:best.name||best.label||best.code,d:km}:null;}
 function show(point){
  if(!active)return;const started=performance.now(),result=profileAt(point);last=result;
  panel?.remove();panel=document.createElement('section');panel.className='duo-point-panel';panel.setAttribute('role','region');panel.setAttribute('aria-label','Point Forecast Meteogram');
  const near=nearestFeature(point),name=point.name||(near?'Near '+near.name+' · '+fmt(near.d,1)+' km':'Map coordinate');
  const climbs=result.rows.map(r=>r.climb).filter(finite),ceilings=result.rows.map(r=>r.ceiling).filter(finite);
  const soar=result.rows.filter(r=>r.soar),status=!soar.length?(result.rows.some(r=>r.soar===null)?'Unavailable / incomplete':'No usable thermals'):soar.some(r=>r.cu>0)?soar.some(r=>r.cu===0)?'Cumulus & blue':'Cumulus':'Blue thermals';
  const retrieval=[...new Set(result.nearest.map(p=>p.retrieved))].sort();
  panel.innerHTML='<header class="duo-point-header"><div><h2>Duo · G-CKML · '+esc(name)+'</h2><small>'+fmt(point.lat,4)+'°, '+fmt(point.lon,4)+'° · '+fmt(result.terrain)+' ft AMSL '+(finite(point.terrain)?'(feature elevation)':'(interpolated model terrain)')+'</small><small>'+esc(data.model)+' · retrieved '+esc(retrieval.length?retrieval.join(' to '):'unavailable')+'</small></div><div><button type="button" data-probe-another title="Hide this chart and choose another map point without disabling the probe.">Pick another point</button> <button type="button" data-probe-close title="Hide the meteogram and restore normal map clicks.">× Close / Turn Off Probe</button></div></header>'+
   '<div class="duo-point-summary"><span><b title="Maximum conservative thermal climb after Duo Discus circling sink, across the displayed two days.">Peak net climb</b>'+fmt(climbs.length?Math.max(...climbs):null,1)+' kt</span><span><b title="The lower of the mixing top and LCL during usable thermal samples. Cloud depth is excluded.">Peak working ceiling</b>'+fmt(ceilings.length?Math.max(...ceilings):null)+' ft AMSL</span><span><b title="Cumulus is possible when mixing reaches the LCL. Blue means the mixing top stays below it.">Thermal type</b>'+status+'</span><span><b>Usable samples · local time</b>'+result.windows.map(esc).join('<br>')+'</span></div>'+
   '<div class="duo-point-notes">'+esc(data.cycle_note)+' '+esc(data.notes)+'<br>Net climb uses the conservative Duo circling estimate. Working ceiling is the lower of mixing top and LCL; cloud depth is not usable gliding height.</div>'+
   '<div class="duo-point-notes">Scroll right for day two; scroll down for the aligned hourly weather strips.</div><div class="duo-point-graphic"><canvas role="img" aria-label="Two-day vertical point forecast. The following hourly table provides numerical values."></canvas></div>'+
   '<details><summary>Accessible hourly values and data coverage</summary><div style="overflow:auto"><table><thead><tr><th>Local time</th><th>Ceiling AMSL / AGL ft</th><th>Net climb kt</th><th>T / Td °C</th><th>Wind / gust kt</th><th>Wind from °T</th><th>MSLP hPa</th></tr></thead><tbody>'+result.rows.map(r=>'<tr><td>'+esc(r.day+' '+r.label+' '+r.zone)+'</td><td>'+fmt(r.ceiling)+' / '+fmt(r.agl)+'</td><td>'+fmt(r.climb,1)+'</td><td>'+fmt(r.temperature,1)+' / '+fmt(r.dewpoint,1)+'</td><td>'+fmt(speed(r.u,r.v),1)+' / '+fmt(r.gust,1)+'</td><td>'+fmt(heading(r.u,r.v))+'</td><td>'+fmt(r.mslp,1)+'</td></tr>').join('')+'</tbody></table></div><p>Samples: '+esc(result.nearest.map(s=>s.name+' ('+fmt(s.d,1)+' km)').join(', ')||'No cached forecast within 120 km')+'. Dashes and hatching mean unavailable.</p></details>';
  container.appendChild(panel);L.DomEvent.disableClickPropagation(panel);L.DomEvent.disableScrollPropagation(panel);
  panel.querySelector('[data-probe-close]').onclick=event=>{event.preventDefault();event.stopPropagation();disable(true);};panel.querySelector('[data-probe-another]').onclick=event=>{event.preventDefault();event.stopPropagation();panel.remove();panel=null;};
  draw(panel.querySelector('canvas'),result);map._duoProbe.lastRenderMs=performance.now()-started;
  requestAnimationFrame(()=>{if(map._duoProbe)map._duoProbe.lastPaintMs=performance.now()-started;});
 }
 function click(event){
  if(!active||event.button!==0||moved||event.target.closest('.leaflet-control,.leaflet-popup,.duo-point-panel,button,input,select,a'))return;
  try {
   const xy=map.mouseEventToContainerPoint(event);let feature=null,nearestPixels=17;
   for(const layer of [map._duoBga,map._duoAirfields])if(layer&&map.hasLayer(layer)){const p=layer.nearest(xy,16);if(p){const pixels=xy.distanceTo(map.latLngToContainerPoint([p.lat,p.lon]));if(pixels<nearestPixels){feature=p;nearestPixels=pixels;}}}
   const latlng=map.mouseEventToLatLng(event),point=feature?{lat:feature.lat,lon:feature.lon,name:feature.name||feature.label,
     terrain:finite(feature.elevation_m)?feature.elevation_m*3.28084:feature.elevation_ft}:{lat:latlng.lat,lon:latlng.lng};
   event.stopImmediatePropagation();event.preventDefault();show(point);
  } catch(error){disable(true);console.warn('Point probe unavailable; normal map controls restored.');}
 }
 const down=e=>{pointerStart=[e.clientX,e.clientY];moved=false;},move=e=>{if(pointerStart&&e.buttons&&Math.hypot(e.clientX-pointerStart[0],e.clientY-pointerStart[1])>5)moved=true;};
 function disable(notify){
  if(!active)return;active=false;container.removeEventListener('click',click,true);container.removeEventListener('pointerdown',down,true);container.removeEventListener('pointermove',move,true);
  map.off('layeradd',added);suspended.forEach(layer=>{if(map.hasLayer(layer))map.on('mousemove',layer.hover,layer);});suspended.clear();
  container.classList.remove('duo-probe-active');panel?.remove();prompt.remove();style.remove();map.off('unload',unload);map._duoProbe=null;
  if(notify)window.duoMapEvent?.('probe_close','off');
 }
 const unload=()=>disable(false);
 container.addEventListener('click',click,true);container.addEventListener('pointerdown',down,true);container.addEventListener('pointermove',move,true);map.on('unload',unload);
 map._duoProbe={show,profileAt,barbParts,fraction,altitude,disable,get last(){return last;},get active(){return active;}};
}
