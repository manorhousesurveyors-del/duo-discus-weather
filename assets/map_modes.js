/* Independent Windy/SkySight-inspired UI. Forecast sources remain explicit. */
function installDuoModes(map, data) {
  'use strict';
  map._duoMode=data.mode;
  if(['wind','thermal','pfd','xc','stf'].includes(data.mode))map._duoClientClicks=true;
  if (map._duoCleanup) map._duoCleanup();
  const style = document.createElement('style');
  style.textContent = `
    .duo-control{font:13px Arial,sans-serif;color:#f4f7fa;background:rgba(21,31,43,.95);border:1px solid #ffffff29;border-radius:18px;box-shadow:0 5px 24px #0005;max-width:min(360px,75vw)}
    .duo-control button,.duo-control select{color:#f4f7fa;background:#293b4c;border:1px solid #ffffff21;border-radius:12px;padding:9px 12px;cursor:pointer;font:inherit;min-height:36px}
    .duo-control button:hover,.duo-control button[aria-pressed=true]{background:#397b8c}.duo-control button:focus-visible{outline:3px solid #f8c55c;outline-offset:2px}
    .duo-menu-toggle{width:100%;text-align:left;font-weight:bold!important}.duo-menu-items{padding:8px;display:grid;gap:5px}.duo-menu-items button{text-align:left}
    .duo-menu-items[hidden]{display:none}.duo-control small{display:block;color:#d4dce3;line-height:1.45;margin:7px 10px;max-width:320px}
    .duo-altitude{display:flex;gap:4px;flex-wrap:wrap;padding:7px;max-width:min(470px,75vw)}.duo-altitude button{font-size:11px;padding:7px}
    .duo-timeline{padding:9px 13px;width:340px;box-sizing:border-box}.duo-timeline input{width:100%;accent-color:#f5b650}.duo-timeline label{display:block;margin-bottom:6px}
    .duo-legend{padding:9px 13px;font-size:11px}.duo-ramp{height:9px;margin:6px 0;border-radius:9px;background:linear-gradient(90deg,#603b91,#3348a4,#2684d2,#39ccda,#63be62,#e4d74f,#f39b38,#d74234)}
    .duo-rain-ramp{position:relative;margin-top:13px}.duo-rain-pointer{position:absolute;top:-12px;transform:translateX(-50%);color:white;font-size:12px;text-shadow:0 1px 3px black}.duo-rain-value{display:block;margin-top:6px;font-weight:bold}
    .duo-labels{display:flex;justify-content:space-between;gap:7px}.duo-callout .leaflet-popup-content-wrapper,.duo-callout .leaflet-popup-tip{background:#172532;color:#f4f7fa}.duo-callout .leaflet-popup-content{margin:13px;min-width:250px;max-width:310px;font:13px Arial;line-height:1.5}
    .duo-callout table{width:100%;border-collapse:collapse}.duo-callout td{padding:4px;border-bottom:1px solid #ffffff22}.duo-callout summary{cursor:pointer;padding:8px 0}.duo-callout .duo-bars{display:flex;align-items:flex-end;height:72px;gap:1px;overflow:hidden}.duo-callout .duo-bars i{flex:1;background:#78bdde;min-width:1px}
    .duo-callout small{display:block;line-height:1.5;margin:6px 0}
    .duo-route-controls{display:flex;flex-wrap:wrap;gap:5px;padding:8px}.duo-route-controls button{font-size:12px}.duo-leg-label{background:#fffef4e8;color:#193d59;padding:3px 6px;border:1px solid #5f7890;border-radius:8px;white-space:nowrap;font:11px Arial}
    @media(prefers-reduced-motion:reduce){.duo-motion-note{display:block!important}}
  `;
  map.getContainer().appendChild(style);
  const controls=[],layers=[];let animation=0,playing=0,alive=true,popup=null,popupObserver=null,popupFrame=0;
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function event(kind,value){
    if(window.duoMapEvent)window.duoMapEvent(kind,value);
  }
  function control(position,cls,markup){
    const item=L.control({position});item.onAdd=()=>{const div=L.DomUtil.create('div','duo-control '+cls);div.innerHTML=markup;L.DomEvent.disableClickPropagation(div);L.DomEvent.disableScrollPropagation(div);return div;};
    item.addTo(map);controls.push(item);return item.getContainer();
  }
  if(!data.hide_menu){
  const menu=control('topright','duo-menu','<button class="duo-menu-toggle" aria-expanded="false" title="Open map modes. Only one main weather mode is shown at a time.">☰ Map modes · '+esc(data.modes[data.mode])+'</button><div class="duo-menu-items" hidden>'+Object.entries(data.modes).map(([key,label])=>'<button data-mode="'+key+'" aria-pressed="'+(key===data.mode)+'" title="'+esc(key==='clean'?'Hide weather overlays and route tools. Your saved task is retained.':'Open '+label+' using the selected forecast model.')+'">'+esc(label)+'</button>').join('')+'</div>');
  const toggle=menu.querySelector('.duo-menu-toggle'),items=menu.querySelector('.duo-menu-items');
  toggle.onclick=()=>{items.hidden=!items.hidden;toggle.setAttribute('aria-expanded',String(!items.hidden));};
  menu.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{button.textContent='Loading…';event('mode',button.dataset.mode);});
  }
  function callout(latlng,markup){
    if(popupObserver)popupObserver.disconnect();cancelAnimationFrame(popupFrame);
    if(popup)map.closePopup(popup);
    // Retain the DOM node: Leaflet update() rewrites string content, which
    // would close details and discard their handlers during layout updates.
    const content=document.createElement('div');content.innerHTML=markup;
    const opened=popup=L.popup({className:'duo-callout',maxWidth:340,maxHeight:350,autoPan:true,autoPanPadding:[20,30]}).setLatLng(latlng).setContent(content).openOn(map);
    opened.getElement().querySelectorAll('details').forEach(d=>d.ontoggle=()=>opened.update());
    // A restored flag can precede Leaflet's stylesheet in the Streamlit iframe.
    // Recalculate its anchor after layout, including late CSS and detail changes.
    const reposition=()=>{cancelAnimationFrame(popupFrame);popupFrame=requestAnimationFrame(()=>{if(alive&&popup===opened&&map.hasLayer(opened))opened.update();});};
    if(typeof ResizeObserver!=='undefined'){popupObserver=new ResizeObserver(reposition);popupObserver.observe(opened.getElement());}
    reposition();
  }
  function nearest(lat,lon){let best=null,distance=Infinity;for(const p of data.samples){const d=Math.hypot((lat-p.lat)*111,(lon-p.lon)*111*Math.cos(lat*Math.PI/180));if(d<distance){best=p;distance=d;}}return distance<=120?{...best,distance}:null;}
  function val(x,digits=1){return x===null||x===undefined?'Unavailable':Number(x).toFixed(digits);}
  function soaringText(lat,lon){const p=nearest(lat,lon);return !p?'No supported forecast sample here.':
    '<b>'+esc(data.modes[data.mode])+'</b><br>Net thermal climb: '+val(p.thermal)+' kt'+
    '<br>PFD: '+val(p.pfd,0)+' km · local-day capacity<br>XC speed: '+val(p.xc,0)+' km/h ('+val(p.xc===null?null:p.xc/1.852)+' kt)'+
    '<br><b>'+(p.stf>0?'Fly at '+val(p.stf,0)+' kt here':'No supported thermal STF here')+'</b>'+
    '<br><small>Source sample '+val(p.distance,0)+' km away. Contours interpolate model estimates; clear areas can mean zero or missing coverage.</small>';}
  if(['thermal','pfd','xc','stf'].includes(data.mode)){
    const field=L.geoJSON(data.contours,{style:feature=>({color:'#18222c',weight:.65,opacity:.8,fillColor:feature.properties.colour,fillOpacity:.63}),
      onEachFeature:(feature,layer)=>{layer.bindTooltip(esc(data.modes[data.mode])+': '+feature.properties.low+'–'+feature.properties.high,{sticky:true});layer.on('mouseover',e=>{layer.setTooltipContent(soaringText(e.latlng.lat,e.latlng.lng));});layer.on('click',e=>callout(e.latlng,soaringText(e.latlng.lat,e.latlng.lng)));}}).addTo(map);layers.push(field);
    const unit={thermal:'Net lift · kt',pfd:'Potential flight distance · km',xc:'XC speed · km/h',stf:'MacCready speed to fly · kt'}[data.mode];
    control('bottomright','duo-legend','<b>'+unit+'</b><div class="duo-ramp"></div><div class="duo-labels">'+data.bands.slice(0,-1).map((x,i)=>'<span>'+x+(i===7?'+':'')+'</span>').join('')+'</div><small>Clear: zero / not soarable / unavailable. '+(data.mode==='pfd'?'PFD is local-day capacity, not a verified remote route.':'Black lines mark estimated value bands.')+'</small>');
    // Negative net climb is shown separately, never confused with climb colours.
    if(data.mode==='thermal')for(const p of data.samples.filter(p=>p.thermal<0)){
      const marker=L.circleMarker([p.lat,p.lon],{radius:5,color:'#626777',fillColor:'#fff',fillOpacity:.9}).bindTooltip('Net sink estimate '+val(p.thermal)+' kt. Thermal climb cannot sustain this polar.').addTo(map);layers.push(marker);
    }
    if(!data.contours.features.length)control('bottomleft','duo-legend','No positive supported contour areas at this time.');
  }
  if(data.mode==='route'&&!map._duoTask){
    const bar=control('bottomleft','duo-route-controls','<button data-action="undo" title="Remove the last point from this date’s task.">↶ Undo last</button><button data-action="clear" title="Clear this route and its completion state.">Clear route</button><button data-action="finish" title="Use the last selected point as Finish and open the task sheet.">✓ Finish here</button><button data-action="home" title="Append Start as Finish and open the task sheet.">⌂ Return home</button><button data-action="optimize" title="Open route distance and optimisation controls below the map.">✦ Optimise route</button><small>Click BGA markers in order: Start → turnpoints → Finish. Finish here or Return home opens your task sheet.</small>');
    bar.querySelectorAll('[data-action]').forEach(button=>button.onclick=()=>event('action',button.dataset.action));
  }
  let radarClick=null;
  if(data.mode==='radar'){
    if(data.rate_grid){
      const g=data.rate_grid,layer=L.imageOverlay(g.image,g.bounds,{opacity:1,attribution:esc(g.source)}).addTo(map);layers.push(layer);
      control('bottomleft','duo-legend','<b>Calibrated radar · '+esc(g.observed_at)+'</b><small>'+esc(g.source)+'</small>');
    }else if(data.radar){
      const layer=L.tileLayer(data.radar.url,{opacity:.8,maxNativeZoom:7,maxZoom:19,attribution:'Radar © RainViewer · observed'}).addTo(map);layers.push(layer);
      control('bottomleft','duo-legend','<b>Observed radar · '+esc(new Date(data.radar.time).toLocaleTimeString('en-GB',{timeZone:'Europe/London'}))+'</b><small>RainViewer Universal Blue · public maximum zoom 7. Enlarging tiles does not add detail.</small>');
    }else control('bottomleft','duo-legend','Observed radar unavailable.');
    const legend=control('bottomright','duo-legend','<b>Point precipitation · mm/h</b><div class="duo-ramp duo-rain-ramp" style="background:linear-gradient(90deg,#2867bb,#35d1d5,#39ae55,#f5e84a,#f88c30,#ed3239,#c836d8)"><span class="duo-rain-pointer" hidden>▼</span></div><div class="duo-labels"><span>0</span><span>0.6</span><span>3</span><span>12</span><span>50</span><span>100</span><span>200+</span></div><span class="duo-rain-value" aria-live="polite">Click the map for a rain estimate</span><small>Open-Meteo hourly mean · selected forecast hour. '+(data.rate_grid?'Calibrated radar grid rates are listed separately.':'RainViewer tile colours use their own scale.')+'</small>');
    const ticks=[0,.6,3,12,50,100,200];
    function markRate(rate,label){
      legend.querySelector('.duo-rain-value').textContent=label;
      const pointer=legend.querySelector('.duo-rain-pointer');pointer.hidden=rate===null;
      if(rate===null)return;
      const capped=Math.min(200,Math.max(0,rate));let i=ticks.findIndex(t=>t>=capped);if(i<1)i=1;
      pointer.style.left=((i-1+(capped-ticks[i-1])/(ticks[i]-ticks[i-1]))/(ticks.length-1)*100)+'%';
      pointer.title=rate.toFixed(1)+' mm/h · Open-Meteo hourly mean';
    }
    function observedText(ll){
      const g=data.rate_grid;if(!g)return '';
      let rate=null;
      if(ll.lat>=g.bounds[0][0]&&ll.lat<=g.bounds[1][0]&&ll.lng>=g.bounds[0][1]&&ll.lng<=g.bounds[1][1]){
        const y=Math.min(g.values.length-1,Math.floor((ll.lat-g.bounds[0][0])/(g.bounds[1][0]-g.bounds[0][0])*g.values.length));
        const x=Math.min(g.values[0].length-1,Math.floor((ll.lng-g.bounds[0][1])/(g.bounds[1][1]-g.bounds[0][1])*g.values[0].length));rate=g.values[y][x];
      }
      return '<hr><b>Calibrated radar: '+(rate===null?'rate unavailable':Number(rate).toFixed(1)+' mm/h')+'</b><br><small>Provider grid-cell rate · '+esc(g.observed_at)+' · '+esc(g.source)+'</small>';
    }
    const localTime=iso=>new Date(iso).toLocaleString('en-GB',{timeZone:'Europe/London',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',timeZoneName:'short'});
    radarClick=(coordinate,result=null)=>{
      const ll=L.latLng(coordinate).wrap();
      const rate=result&&typeof result.rate==='number'&&Number.isFinite(result.rate)?result.rate:null;
      const label=result?(rate===null?'rate unavailable':rate.toFixed(1)+' mm/h'):'loading…';
      markRate(rate,result?(rate===null?'Point estimate unavailable':rate.toFixed(1)+' mm/h · modelled'):'Loading point estimate…');
      let detail='<small>Open-Meteo · modelled hourly mean, not an instantaneous radar measurement.</small>';
      if(result){
        detail+='<small>'+esc(result.model)+' · hour ending '+esc(localTime(result.valid_at))+'</small>';
        if(result.error)detail+='<small>'+esc(result.error)+'</small>';
        if(rate!==null)detail+='<details><summary>Source and hourly period</summary><small>'+esc(localTime(result.interval_start))+' → '+esc(localTime(result.valid_at))+'<br>Precipitation includes rain, showers and snowfall water equivalent.<br>Nearest model grid cell: '+Number(result.grid_latitude).toFixed(4)+', '+Number(result.grid_longitude).toFixed(4)+'<br>Retrieved '+esc(localTime(result.fetched_at))+'.</small></details>';
      }else detail+='<small>Querying this coordinate for the selected forecast hour…</small>';
      callout(ll,'<b>Weather radar: '+label+'</b><br>'+ll.lat.toFixed(4)+', '+ll.lng.toFixed(4)+detail+observedText(ll));
      if(!result)event('radar',ll.lat+':'+ll.lng);
    };
    if(data.radar_point&&new Date(data.radar_point.valid_at).getTime()===new Date(data.when).getTime())
      radarClick([data.radar_point.latitude,data.radar_point.longitude],data.radar_point);
  }
  let windClick=null;
  if(data.mode==='wind'&&data.wind){
    const w=data.wind,signature=w.times[0];let level=0,index=0;
    try{const saved=JSON.parse(sessionStorage.getItem('duo-wind')||'null');if(saved&&saved.signature===signature){level=saved.level;index=saved.index;}else{index=Math.max(0,w.times.findIndex(t=>new Date(t)>=new Date(data.when)));}}catch(e){}
    const save=()=>{try{sessionStorage.setItem('duo-wind',JSON.stringify({signature,level,index}));}catch(e){}};
    const altitude=control('topleft','duo-altitude',w.levels.map((label,i)=>'<button data-level="'+i+'" aria-pressed="'+(i===level)+'" title="Pressure-level height is approximate. Missing levels use log-pressure vector interpolation; underground levels show labelled surface wind.">'+esc(label)+'</button>').join(''));
    const timeline=control('bottomleft','duo-timeline','<label for="duo-wind-time">'+esc(w.labels[index])+'</label><input id="duo-wind-time" aria-label="Five-day hourly wind timeline" title="Move through five days. Grey or empty coverage means this model has no data for that hour." type="range" min="0" max="'+(w.times.length-1)+'" value="'+index+'"><button class="duo-play" title="Play or pause the wind forecast timeline.">▶ Play timeline</button><small>Model forecast · '+esc(w.source)+'. Black arrows move with the wind; barbs point into it. Spatial gap estimates: '+(w.coverage?.spatial_estimates||0)+'; furthest nearest source '+(w.coverage?.max_nearest_source_km||0)+' km. Estimates get less reliable across large gaps.</small>');
    const displayBar=control('topright','duo-altitude','<button data-wind-display title="Switch instantly between arrows moving with the wind and meteorological barbs pointing into it. Map-click inspection works in both views." aria-pressed="false">Wind Display: Streaming Arrows</button>');
    const displayButton=displayBar.querySelector('button');
    control('bottomright','duo-legend','<b>Wind · kt</b><small>Arrows move with the wind.<br>Barbs point into the wind: half = 5 kt, full = 10 kt, triangle = 50 kt. Circle = calm.<br>Click for wind at every altitude.</small>');
    // Wind has no coloured raster at any zoom. Suppress a legacy canvas on
    // initial render and immediately on zoom, including zoom >= 7.
    function hideLegacyRaster(){map.getContainer().querySelectorAll('.duo-wind-heat,.duo-wind-raster').forEach(c=>{c.style.display='none';c.style.filter='none';});}
    const particles=L.DomUtil.create('canvas','duo-wind-particles leaflet-zoom-hide',map.getPanes().overlayPane);
    particles.style.cssText='position:absolute;pointer-events:none;z-index:300;filter:none';
    const pc=particles.getContext('2d'),glyphs=map._duoWindGlyphs,motion=window.matchMedia('(prefers-reduced-motion: reduce)');
    let grid=[],cols=0,rows=0,swarm=[],last=0,display='arrows',barbs=[],spacing=100,drawFrames=0,arrowCount=0;
    let width=0,height=0,ratio=1;
    try{if(sessionStorage.getItem('duo-wind-display')==='barbs')display='barbs';}catch(e){}
    function weights(lat,lon){
      const g=w.grid,ys=g.latitudes,xs=g.longitudes;
      if(lat<ys[0]||lat>ys.at(-1)||lon<xs[0]||lon>xs.at(-1))return [];
      const row=Math.min(ys.length-2,Math.max(0,Math.floor((lat-ys[0])/(ys.at(-1)-ys[0])*(ys.length-1))));
      const col=Math.min(xs.length-2,Math.max(0,Math.floor((lon-xs[0])/(xs.at(-1)-xs[0])*(xs.length-1))));
      const fy=(lat-ys[row])/(ys[row+1]-ys[row]),fx=(lon-xs[col])/(xs[col+1]-xs[col]);
      return [[(1-fx)*(1-fy),g.indices[row][col]],[fx*(1-fy),g.indices[row][col+1]],[(1-fx)*fy,g.indices[row+1][col]],[fx*fy,g.indices[row+1][col+1]]].filter(p=>p[1]!==null&&p[0]>0);
    }
    function sample(weight,t=index,l=level){let u=0,v=0,total=0,interpolated=false,terrain=false,spatial=false,count=0;for(const [f,i] of weight){const a=w.stations[i].values[t]?.[l];if(!a)continue;u+=a[0]*f;v+=a[1]*f;total+=f;count++;interpolated||=!!(a[2]&1);terrain||=!!(a[2]&2);spatial||=!!(a[2]&4);}return total?{u:u/total,v:v/total,interpolated,terrain,spatial,count}:null;}
    function gridVector(x,y){
      const ix=Math.round(x/28),iy=Math.round(y/28);
      return ix<0||ix>=cols||iy<0||iy>=rows?null:grid[iy*cols+ix];
    }
    function seed(p,x=Math.random()*width,y=Math.random()*height){
      p.x=x;p.y=y;p.age=Math.random()*80;p.trail=[];
      const v=gridVector(x,y),speed=v?Math.hypot(v.u,v.v):0;
      if(speed>.01)p.trail=[[x-v.u/speed*18,y+v.v/speed*18],[x,y]];
      return p;
    }
    function paintArrows(advance=false){
      pc.clearRect(0,0,width,height);arrowCount=0;barbs=[];
      for(const p of swarm){
        const v=gridVector(p.x,p.y);
        if(!v||p.age>100||p.x<0||p.x>width||p.y<0||p.y>height){seed(p);continue;}
        if(advance){p.x+=v.u*.14;p.y-=v.v*.14;p.age++;p.trail.push([p.x,p.y]);if(p.trail.length>12)p.trail.shift();}
        if(Math.hypot(v.u,v.v)>.01&&glyphs.arrow(pc,p.trail))arrowCount++;
      }
      drawFrames++;
    }
    function paintBarbs(){
      pc.clearRect(0,0,width,height);barbs=[];arrowCount=0;
      const maxSpeed=grid.reduce((m,v)=>v?Math.max(m,Math.hypot(v.u,v.v)*1.943844492):m,0);
      const extent=glyphs.barbParts(maxSpeed).shaft;
      spacing=Math.max(92+Math.min(6,Math.max(0,map.getZoom()-5))*8,extent*2+44);
      for(let y=spacing/2;y<height-spacing/4;y+=spacing)for(let x=spacing/2;x<width-spacing/4;x+=spacing){
        const ll=map.containerPointToLatLng([x,y]),v=sample(weights(ll.lat,ll.lng));if(!v)continue;
        const barb=glyphs.barb(pc,x,y,v.u,v.v);if(barb)barbs.push({...barb,lat:ll.lat,lon:ll.lng});
      }
      drawFrames++;
    }
    function animate(now){
      animation=0;if(!alive||display!=='arrows'||motion.matches)return;
      if(now-last>=35){last=now;paintArrows(true);}
      animation=requestAnimationFrame(animate);
    }
    function renderDisplay(){
      cancelAnimationFrame(animation);animation=0;
      displayButton.textContent='Wind Display: '+(display==='arrows'?'Streaming Arrows':'Wind Barbs');
      displayButton.setAttribute('aria-pressed',String(display==='barbs'));
      if(display==='barbs')paintBarbs();else{paintArrows();if(!motion.matches)animation=requestAnimationFrame(animate);}
    }
    function rebuild(){
      if(!alive)return;hideLegacyRaster();const size=map.getSize(),origin=map.containerPointToLayerPoint([0,0]);
      width=size.x;height=size.y;ratio=Math.min(2,window.devicePixelRatio||1);
      particles.width=Math.round(width*ratio);particles.height=Math.round(height*ratio);
      particles.style.width=width+'px';particles.style.height=height+'px';L.DomUtil.setPosition(particles,origin);
      pc.setTransform(ratio,0,0,ratio,0,0);pc.imageSmoothingEnabled=false;
      cols=Math.ceil(width/28)+1;rows=Math.ceil(height/28)+1;grid=[];
      for(let y=0;y<rows;y++)for(let x=0;x<cols;x++){
        const ll=map.containerPointToLatLng([x*28,y*28]);grid.push(sample(weights(ll.lat,ll.lng)));
      }
      // Stratified seeding gives every part of the viewport equal coverage.
      swarm=[];const nx=Math.max(1,Math.ceil(width/85)),ny=Math.max(1,Math.ceil(height/85));
      for(let y=0;y<ny;y++)for(let x=0;x<nx;x++)swarm.push(seed({},(x+.2+Math.random()*.6)*width/nx,(y+.2+Math.random()*.6)*height/ny));
      renderDisplay();
    }
    displayButton.onclick=()=>{
      display=display==='arrows'?'barbs':'arrows';
      try{sessionStorage.setItem('duo-wind-display',display);}catch(e){}
      renderDisplay();
    };
    function update(){save();altitude.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.level)===level)));timeline.querySelector('label').textContent=w.labels[index];timeline.querySelector('input').value=index;rebuild();if(lastClick)windClick(lastClick);}
    altitude.querySelectorAll('button').forEach(button=>button.onclick=()=>{level=Number(button.dataset.level);update();});
    timeline.querySelector('input').oninput=e=>{index=Number(e.target.value);update();};
    timeline.querySelector('.duo-play').onclick=e=>{if(playing){clearInterval(playing);playing=0;e.target.textContent='▶ Play timeline';}else{e.target.textContent='Ⅱ Pause timeline';playing=setInterval(()=>{index=(index+1)%w.times.length;update();},1200);}};
    let lastClick=null;
    function windText(v){if(!v)return 'Unavailable';const speed=Math.hypot(v.u,v.v)*1.943844,direction=(Math.atan2(-v.u,-v.v)*180/Math.PI+360)%360;const cardinal=['N','NE','E','SE','S','SW','W','NW'][Math.round(direction/45)%8];return val(speed,0)+' kt · from '+cardinal+' ('+val(direction,0)+'°)'+(v.terrain?' · surface fallback near terrain':(v.interpolated?' · log-pressure interpolation':''))+(v.spatial?' · spatial gap estimate':'');}
    windClick=ll=>{lastClick=ll;const weight=weights(ll.lat,ll.lng),v=sample(weight);
      const vertical=w.levels.map((label,l)=>'<tr><td>'+esc(label)+'</td><td>'+windText(sample(weight,index,l))+'</td></tr>').join('');
      const series=w.times.map((_,t)=>sample(weight,t,level));
      const bars=series.map((p,i)=>'<i style="height:'+(p?Math.min(72,Math.hypot(p.u,p.v)*1.943844*1.3):1)+'px;'+(!p?'background:#555;':'')+'" title="'+esc(w.labels[i]+': '+windText(p))+'"></i>').join('');
      callout(ll,'<b>Wind: '+windText(v)+'</b><br>'+esc(w.labels[index])+'<br>'+ll.lat.toFixed(4)+', '+ll.lng.toFixed(4)+'<details><summary>Vertical wind profile</summary><table>'+vertical+'</table></details><details><summary>Five-day wind timeline</summary><div class="duo-bars">'+bars+'</div><small>Hover a bar for time and wind. Grey = unavailable.</small></details><small>Regular-grid model interpolation at this coordinate. Pressure heights are approximate. Surface fallback is identified above where the requested pressure lies at or below the lowest near-surface sample; unavailable hours are not extrapolated.</small>');
    };
    map._duoWind={sampleAt:(lat,lon,t=index,l=level)=>sample(weights(lat,lon),t,l),bounds:w.grid,coverage:w.coverage,
      get swarm(){return swarm;},get raster(){return grid;},get display(){return display;},get barbs(){return barbs;},
      get renderStats(){return {drawFrames,arrowCount,barbCount:barbs.length,spacing,animationRunning:!!animation,style:glyphs.style};}};
    map.on('zoomstart zoomend',hideLegacyRaster);map.on('moveend resize',rebuild);motion.addEventListener('change',renderDisplay);rebuild();
    layers.push({remove:()=>{map.off('zoomstart zoomend',hideLegacyRaster);map.off('moveend resize',rebuild);motion.removeEventListener('change',renderDisplay);particles.remove();delete map._duoWind;}});
  }
  function click(e){
    if(data.mode==='wind'&&windClick)windClick(e.latlng);
    if(data.mode==='radar'&&radarClick)radarClick(e.latlng);
    if(['thermal','pfd','xc','stf'].includes(data.mode))callout(e.latlng,soaringText(e.latlng.lat,e.latlng.lng));
  }
  map.on('click',click);
  map._duoCleanup=()=>{alive=false;cancelAnimationFrame(animation);cancelAnimationFrame(popupFrame);if(popupObserver)popupObserver.disconnect();if(popup)map.closePopup(popup);clearInterval(playing);map.off('click',click);controls.forEach(c=>c.remove());layers.forEach(l=>l.remove());style.remove();};
  map.on('unload',map._duoCleanup);
}
