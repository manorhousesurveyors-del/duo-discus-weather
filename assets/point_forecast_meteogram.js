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
 .duo-point-panel{position:absolute;z-index:1100;left:10px;right:10px;bottom:10px;max-height:88%;background:#FFFFFF;color:#111111;border:1px solid #C8D0D8;border-radius:14px;box-shadow:0 8px 35px #12232c55;font:14px/1.5 Arial,sans-serif;cursor:auto;overflow:auto}
 .duo-point-header{position:sticky;top:0;z-index:2;background:#122e40;color:#f5fbfd;padding:12px 16px;display:flex;gap:12px;justify-content:space-between;align-items:start}
 .duo-point-header h2{font-size:17px;margin:0}.duo-point-header small{display:block;color:#c6dce2}
 .duo-point-panel button{background:#f5e6bd;color:#172d3b;border:0;border-radius:8px;padding:10px 14px;font:bold 14px Arial;cursor:pointer;white-space:nowrap}
 .duo-point-summary{display:flex;gap:16px;flex-wrap:wrap;padding:10px 16px;background:#edf4f2;border-bottom:1px solid #d3e1df}
 .duo-point-summary b{display:block;color:#11665f}.duo-point-notes{padding:8px 16px;font-size:12px;color:#425563}
 .duo-point-graphic{overflow:auto;padding:0;position:relative;background:#FFFFFF}.duo-point-grid{display:grid;grid-template-columns:1605px;width:1605px}.duo-point-canvas{grid-area:1/1;display:block;width:1605px;height:1046px;max-width:none}.duo-point-labels{grid-area:1/1;position:sticky;left:0;z-index:1;display:block;width:165px;height:1046px;max-width:none;pointer-events:none;background:#FFFFFF;box-shadow:1px 0 #C8D0D8}
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
  const candidates=data.stations.map(station=>({station,d:distance(point,station)})).filter(s=>s.d<=data.max_distance_km).sort((a,b)=>a.d-b.d);
  let nearest=candidates.slice(0,4);const usedStations=new Map();
  if(nearest[0]?.d<.05)nearest=nearest.slice(0,1);
  const raw=nearest.map(s=>1/Math.max(.05,s.d)**2),sum=raw.reduce((a,b)=>a+b,0),weights=raw.map(w=>w/sum);
  const terrain=finite(point.terrain)?point.terrain:nearest.length?weighted(nearest.map(s=>s.station.terrain),weights):null;
  const rows=data.columns.map(column=>{
   const valid=series=>column.indices.every(i=>[0,2,data.fields.indexOf('core')].every(k=>finite(series.hours[i]?.s[k])));
   const options=candidates.map(({station,d})=>{
    const choices=station.series||[{model:data.model,hours:station.hours}];
    return {station,d,series:choices.find(valid)||choices[0]};
   });
   let chosen=options.filter(o=>valid(o.series)).slice(0,4);
   if(!chosen.length)chosen=options.slice(0,4);
   if(chosen[0]?.d<.05)chosen=chosen.slice(0,1);
   const providers=chosen.map(o=>o.series),rawWeights=chosen.map(o=>1/Math.max(.05,o.d)**2),total=rawWeights.reduce((a,b)=>a+b,0),columnWeights=rawWeights.map(w=>w/total);
   chosen.forEach(o=>usedStations.set(o.station.name+'|'+o.series.model,{name:o.station.name+' · '+o.series.model,d:o.d,retrieved:o.series.retrieved||o.station.retrieved}));
   const avg=(part,index,field)=>{
    if(!providers.length)return null;
    const values=providers.map(series=>{
     const [a,b]=column.indices.map(i=>series.hours[i]);
     return part==='s'?mix(a?.s[index],b?.s[index],.5):mix(a?.p[index]?.[field],b?.p[index]?.[field],.5);
    });return weighted(values,columnWeights);
   };
   const row=Object.fromEntries(data.fields.map((name,i)=>[name,avg('s',i)]));
   row.levels=data.levels.map((_,i)=>Array.from({length:5},(_,j)=>avg('p',i,j))).filter(p=>finite(p[0])&&p[0]>terrain);
   row.lcl=finite(terrain)&&finite(row.temperature)&&finite(row.dewpoint)?terrain+400*Math.max(0,row.temperature-row.dewpoint):null;
   row.top=finite(terrain)&&finite(row.depth)?terrain+Math.max(0,row.depth):null;
   row.cu=finite(row.top)&&finite(row.lcl)?Math.max(0,row.top-row.lcl):null;
   const known=[terrain,row.top,row.lcl,row.core,row.sink,row.solar].every(finite);
   row.climb=known?Math.max(0,Math.round((row.core-row.sink)*10)/10):null;
   row.soar=known?row.climb>0&&row.depth>0&&row.solar>0:null;
   row.agl=known?(row.soar?Math.max(0,Math.min(row.depth*(1-.6*(row.sink/row.core)**3),row.lcl-terrain)):0):null;
   row.ceiling=known?terrain+row.agl:null;row.displayMsl=row.ceiling;
   row.sources=[...new Set(providers.flatMap(s=>column.indices.flatMap(i=>s.hours[i]?.sources||[s.model])))];
   row.retrieved=[...new Set(providers.map(s=>s.retrieved).filter(Boolean))];
   const rh=finite(row.temperature)&&finite(row.dewpoint)?clamp(100*Math.exp(17.625*row.dewpoint/(243.04+row.dewpoint)-17.625*row.temperature/(243.04+row.temperature)),0,100):null;
   row.profile=[[terrain+32.8084,rh,row.u,row.v,row.cloud_low],...row.levels].filter(p=>finite(p[0])).sort((a,b)=>a[0]-b[0]);
   row.topU=finite(row.top)?vertical(row.profile,row.top,2):null;row.topV=finite(row.top)?vertical(row.profile,row.top,3):null;
   row.boundaryRh=finite(row.top)?vertical(row.profile,row.top,1):null;
   return {...row,...column};
  });
  const dates=[...new Set(data.columns.map(c=>c.day))];
  const windows=dates.map(day=>{const r=rows.filter(r=>r.day===day),segments=[];let start=null,end=null;
   r.forEach((row,i)=>{if(row.soar){if(start===null)start=row.label;end=row.label;}if(start!==null&&(!row.soar||i===r.length-1)){segments.push(start+'–'+end);start=null;}});
   return day+': '+(segments.join(', ')||(r.some(r=>r.soar===null)?'unavailable':'none'));
  });
  return {point,rows,terrain,nearest:[...usedStations.values()],windows,sun:dates.map((_,i)=>solarTimes(point,data.columns[i*12].iso.slice(0,10))),observation:nearestObservation(point)};
 }

 // Deliberately expand the lowest 7,000 ft; never extrapolate cloud/wind data.
 const ticks=[0,1000,3000,5000,7000,16000,38000],positions=[1,.91,.73,.54,.37,.18,0];
 function fraction(ft){ft=clamp(ft,0,38000);for(let i=0;i<ticks.length-1;i++)if(ft<=ticks[i+1])return mix(positions[i],positions[i+1],(ft-ticks[i])/(ticks[i+1]-ticks[i]));return 0;}
 function altitude(f){for(let i=0;i<positions.length-1;i++)if(f>=positions[i+1])return mix(ticks[i],ticks[i+1],(positions[i]-f)/(positions[i]-positions[i+1]));return 38000;}
 function barbParts(knots){let n=Math.round(Math.max(0,knots)/5)*5;const flags=Math.floor(n/50);n-=flags*50;return {flags,full:Math.floor(n/10),half:n%10>=5};}
 function barb(ctx,x,y,u,v,scale=1){
  const knots=speed(u,v);if(knots===null)return;ctx.save();ctx.translate(x,y);ctx.scale(scale,scale);ctx.strokeStyle='#111111';ctx.fillStyle='#111111';ctx.lineWidth=1.25;
  if(knots<2.5){ctx.beginPath();ctx.arc(0,0,2.5,0,Math.PI*2);ctx.stroke();ctx.restore();return;}
  ctx.rotate(heading(u,v)*Math.PI/180);ctx.beginPath();ctx.moveTo(0,8);ctx.lineTo(0,-16);ctx.stroke();let h=-16;
  const parts=barbParts(knots);for(let i=0;i<parts.flags;i++){ctx.beginPath();ctx.moveTo(0,h);ctx.lineTo(10,h+4);ctx.lineTo(0,h+8);ctx.fill();h+=9;}
  for(let i=0;i<parts.full;i++){ctx.beginPath();ctx.moveTo(0,h);ctx.lineTo(10,h+5);ctx.stroke();h+=4;}
  if(parts.half){if(!parts.full&&!parts.flags)h+=4;ctx.beginPath();ctx.moveTo(0,h);ctx.lineTo(5,h+2.5);ctx.stroke();}ctx.restore();
 }
 function arrow(ctx,x,y,u,v){if(!finite(u)||!finite(v))return;ctx.save();ctx.translate(x,y);ctx.rotate((heading(u,v)+180)*Math.PI/180);ctx.strokeStyle='#111111';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(0,6);ctx.lineTo(0,-6);ctx.moveTo(-3,-2);ctx.lineTo(0,-6);ctx.lineTo(3,-2);ctx.stroke();ctx.restore();}
 // Astronomical sunrise/sunset at the clicked point, with local DST formatting.
 // Solar transit equations use a -0.833° apparent horizon; no weather request.
 const localClock=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/London',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
 function solarTimes(point,date){
  const rad=Math.PI/180,lw=-point.lon*rad,phi=point.lat*rad;
  const d=Date.parse(date+'T12:00:00Z')/86400000+2440587.5-2451545;
  const n=Math.round(d-.0009-lw/(2*Math.PI)),ds=.0009+lw/(2*Math.PI)+n;
  const M=rad*(357.5291+.98560028*ds),L=M+rad*(1.9148*Math.sin(M)+.02*Math.sin(2*M)+.0003*Math.sin(3*M)+102.9372)+Math.PI;
  const dec=Math.asin(Math.sin(L)*Math.sin(rad*23.4397));
  const cos=(Math.sin(rad*-.833)-Math.sin(phi)*Math.sin(dec))/(Math.cos(phi)*Math.cos(dec));
  if(cos>1||cos< -1)return {rise:null,set:null,polar:cos>1?'Polar night':'Midnight sun'};
  const transit=2451545+ds+.0053*Math.sin(M)-.0069*Math.sin(2*L),offset=Math.acos(cos)/(2*Math.PI);
  return {rise:localClock.format(new Date((transit-offset-2440587.5)*86400000)),set:localClock.format(new Date((transit+offset-2440587.5)*86400000)),polar:null};
 }
 function nearestObservation(point,observations=data.observations||[]){
  const usable=observations.filter(o=>finite(o.lat)&&finite(o.lon)&&finite(o.temperature)&&finite(Date.parse(o.time)))
   .map(o=>({...o,d:distance(point,o)})).filter(o=>o.d<=150).sort((a,b)=>a.d-b.d);
  if(!usable.length)return null;
  const station=usable[0],samples=usable.filter(o=>o.station===station.station).sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));return {...samples.at(-1),samples};
 }
 function cloudColumn(row){
  if(!finite(row.top)||!finite(row.lcl))return null;
  const moist=(finite(row.boundaryRh)&&row.boundaryRh>80)||(finite(row.cloud_low)&&row.cloud_low>20);
  if(row.top<row.lcl&&!(row.lcl-row.top<=250&&moist))return null;
  return {base:row.lcl,top:Math.max(row.lcl,row.top),overcast:row.cloud_low>=85,depth:Math.max(0,row.top-row.lcl),nearBase:row.top<=row.lcl};
 }
 function cloudOpacity(row,height){
  if(height<1500)return 0;
  const rh=vertical(row.profile,height,1),pressureCover=vertical(row.profile,height,4);
  const band=height<7000?row.cloud_low:height<16000?row.cloud_mid:row.cloud_high;
  const humidity=finite(rh)?clamp((rh-75)/23,0,1):null;
  const cover=finite(pressureCover)?pressureCover:band;
  if(humidity===null&&!finite(cover))return null;
  return clamp(Math.max(humidity===null?0:humidity*(finite(cover)?.4+.6*cover/100:1),finite(cover)?cover/100*.72:0),0,.92);
 }
 const GRAPH={width:1605,height:1046,left:165,cell:60,sky:66,skyHeight:310};
 const plain=(v,n=0)=>finite(v)?v.toFixed(n):'—';
 function tint(a,b,t){return 'rgb('+a.map((v,i)=>Math.round(v+(b[i]-v)*clamp(t,0,1))).join(',')+')';}
 function draw(canvas,result){
  const {width:W,height:H,left,cell:cw,sky:top,skyHeight:gh}=GRAPH,ratio=Math.min(2,window.devicePixelRatio||1);
  const width=W-left,rows=result.rows,x=i=>left+cw*(i+.5),y=ft=>top+12+(gh-24)*fraction(ft);
  canvas.width=W*ratio;canvas.height=H*ratio;const c=canvas.getContext('2d');c.scale(ratio,ratio);
  const stats={clouds:[],contours:0,barbs:0,observations:0,cloudPixels:{low:0,mid:0,high:0},legendWidth:left,borderedCells:0};
  const ink='#111111',grey='#C8D0D8',red='#E05246',blue='#399DCA';
  c.fillStyle='#FFFFFF';c.fillRect(0,0,W,H);c.textBaseline='middle';c.lineJoin='round';
  const text=(value,xx,yy,colour=ink,align='left',bold=false,size=12)=>{c.font=(bold?'bold ':'')+size+'px Arial';c.fillStyle=colour;c.textAlign=align;c.fillText(value,xx,yy);};
  function box(xx,yy,ww,hh,fill='#FFFFFF'){c.fillStyle=fill;c.fillRect(xx,yy,ww,hh);c.strokeStyle=grey;c.lineWidth=1;c.strokeRect(xx+.5,yy+.5,ww,hh);}
  function legend(lines,yy,hh,colour=ink){box(0,yy,left,hh);lines.forEach((line,i)=>text(line,10,yy+hh/2+(i-(lines.length-1)/2)*16,colour,'left',true));}
  function row(label,yy,value,colour=ink,fill=()=> '#FFFFFF',bold=false,hh=32){
   legend(label,yy,hh,colour);rows.forEach((r,i)=>{box(left+i*cw,yy,cw,hh,fill(r));text(value(r),x(i),yy+hh/2,colour,'center',bold);stats.borderedCells++;});
  }
  // Monotone cubic curves preserve sample extrema and never bridge gaps or nights.
  function path(points){
   if(!points.length)return;c.moveTo(...points[0]);
   const slopes=points.slice(1).map((p,i)=>(p[1]-points[i][1])/(p[0]-points[i][0]));
   const tangent=points.map((_,i)=>!i?slopes[0]:i===points.length-1?slopes[i-1]:slopes[i-1]*slopes[i]<=0?0:2/(1/slopes[i-1]+1/slopes[i]));
   points.slice(1).forEach((p,i)=>{const a=points[i],dx=(p[0]-a[0])/3;c.bezierCurveTo(a[0]+dx,a[1]+tangent[i]*dx,p[0]-dx,p[1]-tangent[i+1]*dx,...p);});
  }
  function curve(field,yy,colour,lineWidth=2,dash=[]){
   c.strokeStyle=colour;c.lineWidth=lineWidth;c.setLineDash(dash);
   for(let day=0;day<2;day++){let points=[];const flush=()=>{if(points.length){c.beginPath();path(points);c.stroke();points=[];}};
    for(let i=day*12;i<(day+1)*12;i++){if(finite(rows[i][field]))points.push([x(i),yy(rows[i][field])]);else flush();}flush();
   }c.setLineDash([]);
  }
  legend(['Point forecast'],0,36);legend(['Time'],36,30);
  for(let day=0;day<2;day++){
   const offset=left+day*12*cw,sun=result.sun?.[day]||solarTimes(result.point,rows[day*12].iso.slice(0,10));
   box(offset,0,12*cw,36);text(rows[day*12].day,offset+16,18,ink,'left',true,15);
   text(sun.polar||('☀ ↑ '+sun.rise+'   ☀ ↓ '+sun.set),offset+12*cw-16,18,'#4C5968','right',false,13);
   text(rows[day*12].zone,offset+12*cw/2,18,'#536476','center');
   for(let i=day*12;i<(day+1)*12;i++){box(left+i*cw,36,cw,30);text(rows[i].label,x(i),51,ink,'center',true);}
  }
  box(0,top,left,gh);box(left,top,width,gh,'#B3DDF2');
  // A bounded, smoothly scaled RH raster keeps every click comfortably local.
  const raster=document.createElement('canvas'),rw=144,rh=112;raster.width=rw;raster.height=rh;
  const rc=raster.getContext('2d'),pixels=rc.createImageData(rw,rh),heights=Array.from({length:rh},(_,j)=>altitude(clamp((j/(rh-1)*gh-12)/(gh-24),0,1)));
  const cloudiness=rows.map(r=>heights.map(h=>cloudOpacity(r,h)));
  for(let ix=0;ix<rw;ix++){
   const day=ix<rw/2?0:1,offset=day*12,pos=clamp((ix-day*rw/2)/(rw/2)*12-.5,0,11),lo=Math.floor(pos)+offset,hi=Math.min(offset+11,lo+1),t=pos%1;
   for(let j=0;j<rh;j++){
    const h=heights[j],opacity=mix(cloudiness[lo][j],cloudiness[hi][j],t),idx=(j*rw+ix)*4;let colour=[179,221,242];
    if(opacity===null)colour=(ix+j)%6<2?[219,226,231]:[237,241,244];
    else if(opacity>0){colour=colour.map((v,k)=>mix(v,[124,134,149][k],opacity));stats.cloudPixels[h<7000?'low':h<16000?'mid':'high']++;}
    for(let k=0;k<3;k++)pixels.data[idx+k]=colour[k];pixels.data[idx+3]=255;
   }
  }
  rc.putImageData(pixels,0,0);c.imageSmoothingEnabled=true;c.drawImage(raster,left,top,width,gh);
  c.save();c.beginPath();c.rect(left,top,width,gh);c.clip();
  if(finite(result.terrain)){c.fillStyle='#D4D5C9';c.fillRect(left,y(result.terrain),width,top+gh-y(result.terrain));}
  // Nested time/height bell contours, capped at the estimated thermal top.
  for(const [threshold,colour] of [[.05,'#4B4FA8'],[.6,'#3B82F6'],[1.2,'#38BDF8'],[2,'#34D399']]){
   for(let day=0;day<2;day++){let points=[];
    const flush=()=>{if(points.length){c.beginPath();path([[points[0][0]-cw/2,y(result.terrain)],...points,[points.at(-1)[0]+cw/2,y(result.terrain)]]);c.closePath();c.fillStyle=colour;c.fill();stats.contours++;points=[];}};
    for(let i=day*12;i<(day+1)*12;i++){const r=rows[i];
     if(r.soar&&r.climb>threshold&&r.solar>0){const height=result.terrain+r.agl*(threshold===.05?1:Math.sqrt((r.climb-threshold)/r.climb));points.push([x(i),y(height)]);}else flush();
    }flush();
   }
  }
  // Shallow cumulus uses one or two scalloped rows; deep clouds stack all the
  // way to the thermal top. Overcast cover darkens/joins these estimated decks.
  rows.forEach((r,i)=>{
   const cloud=cloudColumn(r);if(!cloud)return;
   const base=y(cloud.base)+(cloud.nearBase?3:0),ceiling=cloud.nearBase?base-10:y(cloud.top),depth=base-ceiling;if(depth<=0)return;
   const layers=Math.min(24,Math.max(1,Math.ceil(depth/14))),span=cloud.overcast?cw+2:cw*.86;
   stats.clouds.push({...cloud,index:i,layers,baseY:base,topY:ceiling});
   c.save();c.beginPath();c.rect(x(i)-span/2,ceiling,span,depth);c.clip();
   const grad=c.createLinearGradient(0,ceiling,0,base);grad.addColorStop(0,cloud.overcast?'#C5CDD5':'#FFFFFF');grad.addColorStop(1,cloud.overcast?'#7E8C9C':'#C0CCD7');
   for(let j=0;j<layers;j++){
    const yy=base-(j+.5)*depth/layers,hh=Math.max(8,depth/layers+10),xx=x(i)+[0,-3,3,-1,2][j%5],puff=span*(cloud.overcast?1:[.7,.88,.74,.96,.8][j%5]);
    c.fillStyle=grad;c.strokeStyle=cloud.overcast?'#84909E':'#BCC6CF';c.lineWidth=.6;
    c.beginPath();c.moveTo(xx-puff*.46,yy+hh*.3);
    c.bezierCurveTo(xx-puff*.64,yy-hh*.05,xx-puff*.42,yy-hh*.65,xx-puff*.22,yy-hh*.25);
    c.bezierCurveTo(xx-puff*.19,yy-hh*.9,xx+puff*.19,yy-hh*.9,xx+puff*.23,yy-hh*.25);
    c.bezierCurveTo(xx+puff*.47,yy-hh*.6,xx+puff*.64,yy+hh*.2,xx+puff*.42,yy+hh*.35);
    c.bezierCurveTo(xx+puff*.15,yy+hh*.58,xx-puff*.2,yy+hh*.58,xx-puff*.46,yy+hh*.3);c.closePath();c.fill();c.stroke();
   }c.restore();
  });
  for(const ft of ticks){c.strokeStyle='#FFFFFF77';c.lineWidth=1;c.beginPath();c.moveTo(left,y(ft));c.lineTo(W,y(ft));c.stroke();}
  curve('top',y,'#111111',2.5);curve('lcl',y,'#F4A2B8',2);
  rows.forEach((r,i)=>{for(const ft of [1000,3000,5000,7000,10000,16000,25000,38000]){
   if(ft<=result.terrain)continue;const u=vertical(r.profile,ft,2),v=vertical(r.profile,ft,3);
   if(finite(u)&&finite(v)){barb(c,x(i),y(ft),u,v,.68);stats.barbs++;}
  }});c.restore();
  // Explicit, unambiguous height labels right beside the sky border.
  for(const ft of ticks){const yy=clamp(y(ft),top+9,top+gh-8);text(ft+'ft',left-9,yy,'#333C46','right',false,11);c.strokeStyle='#7F8B98';c.lineWidth=1;c.beginPath();c.moveTo(left-5,y(ft));c.lineTo(left+5,y(ft));c.stroke();}
  text('Thermal Top',10,211,ink,'left',true);text('MSL (ft)',10,227,ink,'left',true);
  text('Condensation',10,262,'#BB5977','left',true);text('Level',10,278,'#BB5977','left',true);
  row(['Soarable Height','MSL (ft)'],376,r=>plain(r.displayMsl),red);
  row(['Soarable Height','AGL (ft)'],408,r=>plain(r.agl),ink,()=> '#FFFFFF',true);
  row(['Thermal Strength (kt)'],440,r=>plain(r.climb,1),ink,r=>finite(r.climb)?tint([255,255,255],[250,220,177],r.climb/5):'#F1F3F5');
  row(['Sfc Heating','W/m²'],472,r=>plain(r.solar),ink,r=>!finite(r.solar)?'#F1F3F5':r.solar<=0?'#FFFFFF':r.solar<100?tint([255,255,255],[254,252,232],r.solar/100):r.solar<300?tint([254,252,232],[254,240,138],(r.solar-100)/200):tint([254,240,138],[253,224,71],(r.solar-300)/300));
  // Forecast temperature/dewpoint and genuine cached observations only.
  box(0,504,left,128);box(left,504,width,128);
  text('Surface Temp (c)',10,522,red,'left',true);text('Surface Dewpoint (c)',10,543,blue,'left',true);
  const obs=result.observation;
  text('Nearest Obs:',10,569,ink,'left',true);
  if(obs){const title=obs.name.length>22?obs.station:obs.name;text(title,10,586,ink,'left',true);text('('+plain(obs.d,1)+'km)',10,603,ink,'left',true,11);text(localClock.format(new Date(obs.time))+' · '+(obs.stale?'stale':'cached'),10,620,'#687787','left',false,11);}
  else{text('Unavailable',10,588,ink,'left',true);text('No cached METAR',10,606,'#687787','left',false,11);}
  const temperatures=[...rows.flatMap(r=>[r.temperature,r.dewpoint]),...(obs?.samples||[]).map(o=>o.temperature)].filter(finite);
  const minT=Math.min(0,...temperatures.map(t=>Math.floor(t/5)*5)),maxT=Math.max(25,...temperatures.map(t=>Math.ceil(t/5)*5));
  const ty=v=>622-(v-minT)/(maxT-minT)*108;
  for(let t=minT;t<=maxT;t+=5){c.strokeStyle='#E2E6EB';c.lineWidth=1;c.beginPath();c.moveTo(left,ty(t));c.lineTo(W,ty(t));c.stroke();text(t+'c',left+4,ty(t)-6,'#677787','left',false,10);}
  curve('temperature',ty,red,2);curve('dewpoint',ty,blue,2);
  if(obs){
   for(let day=0;day<2;day++){
    const start=Date.parse(rows[day*12].iso),end=Date.parse(rows[day*12+11].iso);let previous=null;
    for(const sample of obs.samples){const when=Date.parse(sample.time);if(when<start||when>end)continue;
     const point=[x(day*12)+(when-start)/3600000*cw,ty(sample.temperature)];
     if(previous&&when-previous.time<=3*3600000){c.beginPath();c.moveTo(...previous.point);c.lineTo(...point);c.setLineDash([5,4]);c.strokeStyle=red;c.lineWidth=2;c.stroke();c.setLineDash([]);}
     c.fillStyle=red;c.beginPath();c.arc(...point,2.6,0,Math.PI*2);c.fill();previous={point,time:when};stats.observations++;
    }
   }
  }
  row(['Temperature (c)'],632,r=>plain(r.temperature,1),red,()=> '#F0EDFA',false,28);
  row(['Dewpoint (c)'],660,r=>plain(r.dewpoint,1),blue,()=> '#FFFFFF',false,28);
  function windCells(yy,hh,upper){
   box(0,yy,left,hh);
   if(upper){text('Thermal Top Wind',10,yy+28,ink,'left',true);text('From °T · kt',10,yy+49,'#536476');}
   else{text('Wind (10m AGL)',10,yy+24,ink,'left',true);text('2m unavailable',10,yy+43,'#536476','left',false,11);text('Gusts',10,yy+80,ink,'left',true);}
   rows.forEach((r,i)=>{
    const u=upper?r.topU:r.u,v=upper?r.topV:r.v,knots=speed(u,v),bearing=heading(u,v),dark=knots>=32;
    const colour=!finite(knots)?'#F1F3F5':knots<=30?tint([220,227,249],[91,110,225],knots/30):tint([91,110,225],[59,76,202],(knots-30)/20);
    box(left+i*cw,yy,cw,hh,colour);stats.borderedCells++;
    if(dark){c.fillStyle='#DCE3F9';c.fillRect(x(i)-12,yy+4,24,22);}arrow(c,x(i),yy+15,u,v);
    text(finite(knots)?plain(knots)+'kt':'—',x(i),yy+37,dark?'#FFFFFF':ink,'center');
    text(finite(bearing)?plain(bearing)+'°':'—',x(i),yy+57,dark?'#FFFFFF':ink,'center');
    if(!upper)text(finite(r.gust)?plain(r.gust)+'kt':'—',x(i),yy+80,dark?'#FFFFFF':ink,'center',true,13);
   });
  }
  windCells(688,96,false);windCells(784,76,true);
  legend(['Pressure (hPa)'],860,156,blue);box(left,860,width,128);
  const py=v=>978-(clamp(v,980,1020)-980)/40*106;
  for(const hpa of [980,990,1000,1010,1020]){c.strokeStyle='#E2E6EB';c.lineWidth=1;c.beginPath();c.moveTo(left,py(hpa));c.lineTo(W,py(hpa));c.stroke();text(hpa+'hPa',left+4,py(hpa)-6,'#5B95B0','left',false,10);}
  curve('mslp',py,blue,2);rows.forEach((r,i)=>{if(finite(r.mslp)&&(r.mslp<980||r.mslp>1020)){const yy=py(r.mslp),dir=r.mslp<980?1:-1;c.fillStyle=blue;c.beginPath();c.moveTo(x(i)-4,yy);c.lineTo(x(i)+4,yy);c.lineTo(x(i),yy+dir*5);c.fill();}});
  rows.forEach((r,i)=>{box(left+i*cw,988,cw,28);text(plain(r.mslp,1),x(i),1002,blue,'center');stats.borderedCells++;});
  box(0,1016,W,30);text('AMSL = above sea level · AGL = above ground · Black: mixing top; usable height excludes cloud · Clouds are model estimates · Dashed red / dots: cached observations only',10,1031,'#536476','left',false,11);
  // Continuous time columns across charts and clearly separated local days.
  c.strokeStyle=grey;c.lineWidth=1;
  for(let i=0;i<=24;i++){const xx=left+i*cw+.5;c.beginPath();c.moveTo(xx,36);c.lineTo(xx,1016);c.stroke();}
  c.strokeStyle='#748393';c.lineWidth=1.5;c.beginPath();c.moveTo(left+12*cw,0);c.lineTo(left+12*cw,1016);c.stroke();
  return stats;
 }
 function nearestFeature(point){const records=[...(map._duoBga?.records||[]),...(map._duoAirfields?.records||[])];let best=null,km=Infinity;for(const row of records){const d=distance(point,row);if(d<km){best=row;km=d;}}return best&&km<30?{name:best.name||best.label||best.code,d:km}:null;}
 function show(point){
  if(!active)return;const started=performance.now(),result=profileAt(point);last=result;
  panel?.remove();panel=document.createElement('section');panel.className='duo-point-panel';panel.setAttribute('role','region');panel.setAttribute('aria-label','Point Forecast Meteogram');
  const near=nearestFeature(point),name=point.name||(near?'Near '+near.name+' · '+fmt(near.d,1)+' km':'Map coordinate');
  const climbs=result.rows.map(r=>r.climb).filter(finite),ceilings=result.rows.map(r=>r.ceiling).filter(finite);
  const soar=result.rows.filter(r=>r.soar),status=!soar.length?(result.rows.some(r=>r.soar===null)?'Unavailable / incomplete':'No usable thermals'):soar.some(r=>r.cu>0)?soar.some(r=>r.cu===0)?'Cumulus & blue':'Cumulus':'Blue thermals';
  const retrieval=[...new Set(result.rows.flatMap(r=>r.retrieved))].sort();
  const modelNote=result.rows.map(r=>r.day+' '+r.label+': '+r.sources.join(' + ')).join('; ');
  panel.innerHTML='<header class="duo-point-header"><div><h2>Duo · G-CKML · '+esc(name)+'</h2><small>'+fmt(point.lat,4)+'°, '+fmt(point.lon,4)+'° · '+fmt(result.terrain)+' ft AMSL '+(finite(point.terrain)?'(feature elevation)':'(interpolated model terrain)')+'</small><small>'+esc(data.model)+' · '+esc(data.loading||'dry')+' · retrieved '+esc(retrieval.length?retrieval.join(' to '):'unavailable')+'</small></div><div><button type="button" data-probe-another title="Hide this chart and choose another map point without disabling the probe.">Pick another point</button> <button type="button" data-probe-close title="Hide the meteogram and restore normal map clicks.">× Close / Turn Off Probe</button></div></header>'+
   '<div class="duo-point-summary"><span><b title="Estimated core updraught minus the selected Duo Discus polar circling sink once; floored at zero.">Peak net climb</b>'+fmt(climbs.length?Math.max(...climbs):null,1)+' kt</span><span><b title="Estimated upper zero-net-climb height in the thermal column, capped below cloud base.">Peak working ceiling</b>'+fmt(ceilings.length?Math.max(...ceilings):null)+' ft AMSL</span><span><b title="Cumulus is possible when mixing reaches the LCL. Blue means the mixing top stays below it.">Thermal type</b>'+status+'</span><span><b>Usable samples · local time</b>'+result.windows.map(esc).join('<br>')+'</span></div>'+
   '<div class="duo-point-notes">'+esc(data.cycle_note)+' '+esc(data.notes)+'<br>Net climb = max(0, 1.35 w* − polar circling sink), rounded once. Selected loading: '+esc(data.loading||'dry')+'. Black thermal top = estimated mixing top. Pink = condensation level. The table excludes cloud depth from usable height; an unsoarable MSL cell shows terrain, with 0 ft AGL. Cloud stacks are estimates, not a thunderstorm forecast.</div>'+
   '<div class="duo-point-notes">Scroll right for day two; scroll down for the aligned hourly weather strips.</div><div class="duo-point-graphic"><div class="duo-point-grid"><canvas class="duo-point-canvas" role="img" aria-label="Two-day vertical point forecast with bordered hourly values. The following table provides accessible numbers."></canvas><canvas class="duo-point-labels" aria-hidden="true"></canvas></div></div>'+
   '<details><summary>Accessible hourly values and data coverage</summary><div style="overflow:auto"><table><thead><tr><th>Local time</th><th>Ceiling AMSL / AGL ft</th><th>Net climb kt</th><th>Raw core kt</th><th>Circling sink kt</th><th>T / Td °C</th><th>Wind / gust kt</th><th>Wind from °T</th><th>MSLP hPa</th><th>Model</th></tr></thead><tbody>'+result.rows.map(r=>'<tr><td>'+esc(r.day+' '+r.label+' '+r.zone)+'</td><td>'+fmt(r.displayMsl)+' / '+fmt(r.agl)+'</td><td>'+fmt(r.climb,1)+'</td><td>'+fmt(r.core,2)+'</td><td>'+fmt(r.sink,2)+'</td><td>'+fmt(r.temperature,1)+' / '+fmt(r.dewpoint,1)+'</td><td>'+fmt(speed(r.u,r.v),1)+' / '+fmt(r.gust,1)+'</td><td>'+fmt(heading(r.u,r.v))+'</td><td>'+fmt(r.mslp,1)+'</td><td>'+esc(r.sources.join(' + '))+'</td></tr>').join('')+'</tbody></table></div><p>Hourly model sources: '+esc(modelNote)+'</p><p>Samples: '+esc(result.nearest.map(s=>s.name+' ('+fmt(s.d,1)+' km)').join(', ')||'No cached forecast within 120 km')+'. Dashes and hatching mean unavailable.</p>'+ (result.observation?'<p>Nearest cached observations: '+esc(result.observation.name)+' · '+fmt(result.observation.d,1)+' km. '+result.observation.samples.map(o=>esc(o.time)+' · '+fmt(o.temperature,1)+'°C'+(o.stale?' (stale)':'')).join('; ')+'. Dashed segments join reports no more than three hours apart; no forecast bias or future observations are invented.</p>':'<p>No cached observations are available. The temperature lines show model forecasts only.</p>')+'</details>';
  container.appendChild(panel);L.DomEvent.disableClickPropagation(panel);L.DomEvent.disableScrollPropagation(panel);
  panel.querySelector('[data-probe-close]').onclick=event=>{event.preventDefault();event.stopPropagation();disable(true);};panel.querySelector('[data-probe-another]').onclick=event=>{event.preventDefault();event.stopPropagation();panel.remove();panel=null;};
  const canvas=panel.querySelector('.duo-point-canvas');map._duoProbe.lastRenderDetails=draw(canvas,result);
  const labels=panel.querySelector('.duo-point-labels'),ratio=canvas.width/GRAPH.width;
  labels.width=GRAPH.left*ratio;labels.height=canvas.height;labels.getContext('2d').drawImage(canvas,0,0,labels.width,labels.height,0,0,labels.width,labels.height);
  map._duoProbe.lastRenderMs=performance.now()-started;
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
 map._duoProbe={show,profileAt,barbParts,fraction,altitude,solarTimes,nearestObservation,cloudColumn,cloudOpacity,render:draw,disable,get last(){return last;},get active(){return active;}};
}
