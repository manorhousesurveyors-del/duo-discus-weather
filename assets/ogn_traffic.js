function installDuoTraffic(map,data){
 'use strict';
 const esc=window.duoEsc,maxAge=(data.maxAgeSeconds||900)*1000;
 let mode='live',filter='all',timer=0,expiryTimer=0,seconds=20,body=null,busy=false,demoTick=0,popup=null,popupId=null;
 const age=p=>Math.max(0,Math.round((Date.now()-Date.parse(p.observed_at))/1000));
 const layer=new DuoCanvasPoints([],{customLabels:true,drawPoint:(c,p,x,y)=>{
   c.save();c.globalAlpha=age(p)>120?.65:1;
   c.translate(x,y);c.rotate(p.heading*Math.PI/180);c.beginPath();c.moveTo(0,-13);c.lineTo(5,3);c.lineTo(0,0);c.lineTo(-5,3);c.closePath();
   c.fillStyle=p.colour;c.fill();c.strokeStyle='#132635';c.lineWidth=1.2;c.stroke();c.rotate(-p.heading*Math.PI/180);
   const label=p.competition_id||p.registration,width=c.measureText(label).width+10;
   c.fillStyle=p.colour;c.fillRect(7,-9,width,19);c.strokeStyle=p.highlight?'#ff2d56':'#132635';c.lineWidth=p.highlight?3:1;c.strokeRect(7,-9,width,19);
   c.fillStyle='#102331';c.fillText(label,12,5);c.restore();return {left:x+7,right:x+7+width,top:y-9,bottom:y+10};
  },tooltip:p=>markup(p),onClick:p=>{popupId=p.id;popup=L.popup({maxWidth:320}).setLatLng([p.lat,p.lon]).setContent(markup(p)).openOn(map);}});
 function markup(p){const vario=Number.isFinite(p.vario_kt)?(p.vario_kt>=0?'+':'')+p.vario_kt.toFixed(1)+' kt climb / sink':'Vario unavailable';
  return '<b>'+(mode==='demo'?'DEMO · ':'')+esc(p.registration)+(p.competition_id?' · '+esc(p.competition_id):'')+'</b><br>'+esc(p.aircraft_model||({1:'Glider',2:'Tug',8:'Powered / motor glider'}[p.aircraft_type]||'Unidentified FLARM target'))+'<br>'+p.altitude_ft.toLocaleString('en-GB')+' ft · '+p.groundspeed_kt+' kt · '+Math.round(p.heading)+'°T<br><b>'+vario+'</b><br><b>Position age: '+age(p)+' seconds'+(age(p)>120?' · OLD POSITION':'')+'</b><br><small>'+esc(p.source||'OGN')+' · '+esc(p.receiver||'')+'<br>GNSS-derived vario. Airborne filter uses speed ≥14 kt; it cannot confirm flight.<br>Reception is incomplete. Not collision avoidance.</small>';
 }
 const panel=L.control({position:'bottomright'});
 panel.onAdd=()=>{const el=L.DomUtil.create('div','duo-ogn-panel');el.style.cssText='background:#172532ed;color:white;padding:10px;border-radius:13px;max-width:350px;font:12px/1.5 Arial';
  el.innerHTML='<b>Live OGN Glider Radar</b><div class="duo-ogn-state" aria-live="polite">Loading…</div><div style="display:flex;gap:5px;margin:7px 0"><button data-ogn="refresh" title="Read the newest shared OGN snapshot without reloading the forecast.">Refresh now</button><select data-ogn="interval" aria-label="OGN auto-refresh" title="Poll every 15, 20 or 30 seconds. The receiver stays in the background."><option value="15">15 seconds</option><option selected value="20">20 seconds</option><option value="30">30 seconds</option><option value="0">Paused</option></select><button data-ogn="demo" title="Switch to clearly labelled synthetic playback for practising at night.">Demo</button></div><label title="All recent includes stationary aircraft. Airborne is a groundspeed proxy, not confirmed flight.">Targets <select data-ogn="filter" aria-label="OGN target filter"><option value="all">All recent · 15 min</option><option value="airborne">Airborne / moving</option></select></label><small style="display:block;margin-top:7px">Yellow: glider · cyan: sink · amber: strong climb.<br>Faded: position older than 2 min. Pink border: G-CKML.<br><a href="https://live.glidernet.org/#c=53.5,-3&z=6" target="_blank" rel="noopener" style="color:#96d3ff">Open official OGN viewer</a></small>';
  L.DomEvent.disableClickPropagation(el);L.DomEvent.disableScrollPropagation(el);return el;};
 function showStatus(text){const el=panel.getContainer()?.querySelector('.duo-ogn-state');if(el)el.textContent=text;}
 function render(){const now=Date.now();let aircraft=[];
  if(mode==='demo'){
   const t=demoTick++/8;aircraft=Array.from({length:7},(_,i)=>({id:'demo'+i,lat:52.52+.23*Math.sin(t+i*.8),lon:-2.88+.38*Math.cos(t+i*.8),registration:'DEMO '+(i+1),competition_id:'',altitude_ft:Math.round(4200+900*Math.sin(t+i)),groundspeed_kt:55,heading:(t*180/Math.PI+i*45)%360,vario_kt:Math.round(Math.sin(t+i)*45)/10,observed_at:new Date().toISOString(),highlight:false,airborne:true}));
   showStatus('DEMO PLAYBACK · synthetic gliders · not live');
  }else if(data.recorded){showStatus('Recorded weather mode · Live feed disabled. Choose Demo to practise.');}
  else if(body){
   const recent=(body.aircraft||[]).filter(a=>now-Date.parse(a.observed_at)<=maxAge&&Date.parse(a.observed_at)<=now+30000);
   aircraft=recent.filter(a=>filter==='all'||a.airborne);
   const fresh=recent.filter(a=>age(a)<=120).length;
   showStatus(aircraft.length+' targets · '+fresh+' updated within 2 min · '+recent.length+' in last 15 min. '+
    (now-Date.parse(body.fetched_at)>60000||body.status!=='live'?'Feed delayed; retained positions only. ':recent.length?'':'No recent gliders received. '));
  }else showStatus('Connecting to live UK glider feed…');
  if(filter==='airborne')aircraft=aircraft.filter(a=>a.airborne);
  if(popup){const p=aircraft.find(p=>p.id===popupId);if(!p){map.closePopup(popup);popup=null;popupId=null;}else popup.setContent(markup(p)).setLatLng([p.lat,p.lon]);}
  layer.records=aircraft.map(p=>({...p,label:p.competition_id||p.registration,priority:p.highlight,
   colour:p.vario_kt<-.5?'#65dce9':p.vario_kt>=2?'#ffb442':'#ffe364'}));layer.schedule();
  map._duoTraffic.status=mode;map._duoTraffic.count=aircraft.length;
 }
 async function refresh(){
  if(busy||!map.hasLayer(layer))return;if(mode==='demo'||data.recorded){render();return;}
  busy=true;const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
  try{
   const root=new URLSearchParams(location.search).get('streamlitUrl')||location.origin+'/';
   const url=new URL(data.path,root.endsWith('/')?root:root+'/');url.searchParams.set('t',String(Date.now()));
   const r=await fetch(url,{cache:'no-store',signal:controller.signal});if(!r.ok)throw Error('HTTP '+r.status);
   const next=await r.json();if(next.schema!==1||!Array.isArray(next.aircraft)||next.aircraft.length>3000)throw Error('Unexpected traffic data');
   body=next;render();
  }catch(e){if(body)body={...body,status:'degraded'};render();showStatus('Live feed unavailable. Retaining unexpired positions; Demo is available.');}
  finally{clearTimeout(timeout);busy=false;}
 }
 function schedule(){clearInterval(timer);if(seconds)timer=setInterval(refresh,mode==='demo'?1000:seconds*1000);}
 layer.on('add',()=>{
  panel.addTo(map);const el=panel.getContainer();el.querySelector('[data-ogn="refresh"]').onclick=refresh;
  el.querySelector('[data-ogn="interval"]').value=String(seconds);el.querySelector('[data-ogn="interval"]').onchange=e=>{seconds=Number(e.target.value);schedule();};
  el.querySelector('[data-ogn="filter"]').value=filter;el.querySelector('[data-ogn="filter"]').onchange=e=>{filter=e.target.value;render();};
  el.querySelector('[data-ogn="demo"]').textContent=mode==='demo'?'Back to Live':'Demo';
  el.querySelector('[data-ogn="demo"]').onclick=e=>{mode=mode==='live'?'demo':'live';e.target.textContent=mode==='demo'?'Back to Live':'Demo';render();schedule();refresh();};
  refresh();schedule();expiryTimer=setInterval(()=>{if(mode==='live')render();},5000);
 });
 layer.on('remove',()=>{clearInterval(timer);clearInterval(expiryTimer);panel.remove();if(popup){map.closePopup(popup);popup=null;popupId=null;}});
 map._duoTraffic={layer,refresh,get body(){return body;},status:'live',count:0};
 duoRegisterLayer(map,'Live OGN Gliders (FLARM)',layer);if(data.show)layer.addTo(map);
 window.addEventListener('pagehide',()=>{clearInterval(timer);clearInterval(expiryTimer);});
}
