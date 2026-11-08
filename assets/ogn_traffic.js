function installDuoTraffic(map,data){
 'use strict';
 const esc=window.duoEsc,maxAge=(data.maxAgeSeconds||1200)*1000;
 let timer=0,expiryTimer=0,warmTimer=0,seconds=15,body=null,busy=false,popup=null,popupId=null;
 const warmUntil=performance.now()+4500;
 const filters={glider:true,other:true,ground:true};
 const number=(v,unit)=>Number.isFinite(v)?Math.round(v).toLocaleString('en-GB')+unit:'unknown';
 const age=p=>Math.max(0,Math.round((Date.now()-Date.parse(p.observed_at))/1000));
 const layer=new DuoCanvasPoints([],{customLabels:true,drawPoint:(c,p,x,y)=>{
   c.save();c.globalAlpha=age(p)>120?.65:1;
   c.translate(x,y);c.rotate((p.heading||0)*Math.PI/180);c.beginPath();if(Number.isFinite(p.heading)){c.moveTo(0,-13);c.lineTo(5,3);c.lineTo(0,0);c.lineTo(-5,3);c.closePath();}else c.arc(0,0,3,0,2*Math.PI);
   c.fillStyle=p.colour;c.fill();c.strokeStyle='#132635';c.lineWidth=1.2;c.stroke();c.rotate(-(p.heading||0)*Math.PI/180);
   const label=p.badge,width=c.measureText(label).width+10;
   c.fillStyle=p.colour;c.fillRect(7,-9,width,19);c.strokeStyle=p.highlight?'#ff2d56':'#132635';c.lineWidth=p.highlight?3:1;c.strokeRect(7,-9,width,19);
   c.fillStyle='#102331';c.fillText(label,12,5);c.restore();return {left:x+7,right:x+7+width,top:y-9,bottom:y+10};
  },tooltip:p=>markup(p),onClick:p=>{popupId=p.id;popup=L.popup({maxWidth:320}).setLatLng([p.lat,p.lon]).setContent(markup(p)).openOn(map);}});
 function markup(p){const vario=Number.isFinite(p.vario_kt)?(p.vario_kt>=0?'+':'')+p.vario_kt.toFixed(1)+' kt climb / sink':'Vario unavailable';
  return '<b>'+esc(p.registration)+(p.competition_id?' · '+esc(p.competition_id):'')+'</b><br>'+esc(p.aircraft_model||({1:'Glider',2:'Tug',8:'Powered / motor glider'}[p.aircraft_type]||'Unidentified FLARM target'))+'<br>'+number(p.altitude_ft,' ft')+' · '+number(p.groundspeed_kt,' kt')+' · '+number(p.heading,'°T')+'<br><b>'+vario+'</b><br><b>Position age: '+age(p)+' seconds'+(age(p)>120?' · OLD POSITION':'')+'</b><br><small>'+esc(p.source||'OGN')+' · '+esc(p.receiver||'')+' · '+esc(p.timestamp_kind||'packet UTC')+'<br>GNSS-derived vario. Airborne filter uses speed ≥14 kt; it cannot confirm flight.<br>Reception is incomplete. Not collision avoidance.</small>';
 }
 const panel=L.control({position:'topleft'});
 panel.onAdd=()=>{const el=L.DomUtil.create('div','duo-ogn-panel');el.style.cssText='background:#172532ed;color:white;padding:10px;border-radius:13px;max-width:370px;font:12px/1.5 Arial';
  el.innerHTML='<b>Live OGN Glider Radar</b><div class="duo-ogn-state" aria-live="polite">Connecting…</div><div style="display:flex;gap:5px;margin:7px 0"><button data-ogn="refresh" title="Read the newest genuine telemetry snapshot without reloading the forecast.">Refresh now</button><select data-ogn="interval" aria-label="OGN auto-refresh" title="Poll every 15, 20 or 30 seconds. The receiver runs continuously across pages."><option selected value="15">15 seconds</option><option value="20">20 seconds</option><option value="30">30 seconds</option><option value="0">Paused</option></select></div>'+[
    ['glider','Show Gliders (Yellow)','Show identified gliders and motor gliders.'],
    ['other','Show Powered / Unregistered FLARM (Blue)','Show powered aircraft and unidentified targets received from OGN.'],
    ['ground','Include Ground / Stationary Targets','Include slow and stationary positions. Movement uses groundspeed, not confirmed take-off.']
   ].map(([key,label,help])=>'<label style="display:block" title="'+help+'"><input type="checkbox" data-ogn-filter="'+key+'" checked> '+label+'</label>').join('')+
   '<small style="display:block;margin-top:7px">Faded: position older than 2 min. Expires after 20 min.<br>Pink border: G-CKML. All positions come from telemetry.<br><a href="https://live.glidernet.org/#c=53.5,-3&z=6" target="_blank" rel="noopener" style="color:#96d3ff">Open official OGN viewer</a></small>';
  L.DomEvent.disableClickPropagation(el);L.DomEvent.disableScrollPropagation(el);return el;};
 function showStatus(text){const el=panel.getContainer()?.querySelector('.duo-ogn-state');if(el)el.textContent=text;}
 function render(){const now=Date.now();let aircraft=[];
  if(data.recorded){showStatus('Live telemetry is disabled in recorded weather mode. 0 live targets.');}
  else if(body){
   const recent=(body.aircraft||[]).filter(a=>now-Date.parse(a.observed_at)<=maxAge&&Date.parse(a.observed_at)<=now+30000);
   aircraft=recent.filter(a=>filters[a.category||'other']&&(filters.ground||a.airborne===true));
   const fresh=recent.filter(a=>age(a)<=120).length;
   showStatus((body.source||'No source connected')+' · '+fresh+' positions ≤2 min · '+recent.length+' recent ≤20 min · '+aircraft.length+' shown. '+
    (now-Date.parse(body.fetched_at)>60000||body.status!=='live'?'Feed delayed; retained positions only. ':recent.length?'':'No recent telemetry received. '));
  }else showStatus('Connecting to OGN APRS-IS / live.glidernet.org XML · 0 received targets.');
  if(popup){const p=aircraft.find(p=>p.id===popupId);if(!p){map.closePopup(popup);popup=null;popupId=null;}else popup.setContent(markup(p)).setLatLng([p.lat,p.lon]);}
  layer.records=aircraft.map(p=>({...p,label:p.badge,priority:p.highlight,colour:p.category==='glider'?'#FDE047':'#38BDF8'}));layer.schedule();
  map._duoTraffic.status=data.recorded?'disabled':body?.status||'connecting';map._duoTraffic.count=aircraft.length;
 }
 async function refresh(){
  if(busy||!map.hasLayer(layer))return;if(data.recorded){render();return;}
  busy=true;const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
  try{
   const root=new URLSearchParams(location.search).get('streamlitUrl')||location.origin+'/';
   const url=new URL(data.path,root.endsWith('/')?root:root+'/');url.searchParams.set('t',String(Date.now()));
   const r=await fetch(url,{cache:'no-store',signal:controller.signal});if(!r.ok)throw Error('HTTP '+r.status);
   const next=await r.json();if(next.schema!==2||!Array.isArray(next.aircraft)||next.aircraft.length>20000)throw Error('Unexpected traffic data');
   body=next;render();
  }catch(e){if(body){body={...body,status:'degraded'};render();}else showStatus('No live source connected · 0 targets. Telemetry endpoint unavailable.');}
  finally{clearTimeout(timeout);busy=false;
   clearTimeout(warmTimer);
   if(map.hasLayer(layer)&&!body?.aircraft?.length&&performance.now()<warmUntil)warmTimer=setTimeout(refresh,750);
  }
 }
 function schedule(){clearInterval(timer);if(seconds)timer=setInterval(refresh,seconds*1000);}
 layer.on('add',()=>{
  panel.addTo(map);const el=panel.getContainer();el.querySelector('[data-ogn="refresh"]').onclick=refresh;
  el.querySelector('[data-ogn="interval"]').value=String(seconds);el.querySelector('[data-ogn="interval"]').onchange=e=>{seconds=Number(e.target.value);schedule();};
  el.querySelectorAll('[data-ogn-filter]').forEach(input=>{input.checked=filters[input.dataset.ognFilter];input.onchange=()=>{filters[input.dataset.ognFilter]=input.checked;render();};});
  refresh();schedule();expiryTimer=setInterval(render,5000);
 });
 layer.on('remove',()=>{clearInterval(timer);clearInterval(expiryTimer);clearTimeout(warmTimer);panel.remove();if(popup){map.closePopup(popup);popup=null;popupId=null;}});
 map._duoTraffic={layer,refresh,get body(){return body;},status:'live',count:0};
 duoRegisterLayer(map,'Live OGN Gliders (FLARM)',layer);if(data.show)layer.addTo(map);
 window.addEventListener('pagehide',()=>{clearInterval(timer);clearInterval(expiryTimer);clearTimeout(warmTimer);});
}
