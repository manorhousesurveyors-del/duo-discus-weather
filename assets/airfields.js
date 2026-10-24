function installDuoAirfields(map,data) {
  'use strict';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const val=(v,n=1)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(n):'unavailable';
  const time=t=>t?new Date(typeof t==='number'?t*1000:t).toLocaleString('en-GB',{timeZone:'Europe/London',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',timeZoneName:'short'}):'unavailable';
  const group=L.featureGroup().addTo(map),cache=new Map(),pending=new Map();
  const style=document.createElement('style');style.textContent='.duo-airfield-popup .leaflet-popup-content-wrapper,.duo-airfield-popup .leaflet-popup-tip{background:#172532;color:#f4f7fa}.duo-airfield-popup .leaflet-popup-content{font:13px/1.5 Arial;max-width:410px}.duo-airfield-popup pre{white-space:pre-wrap;font:11px/1.5 monospace}.duo-airfield-popup table{width:100%;font-size:11px}.duo-airfield-popup td{padding:3px}.duo-airfield-popup small{display:block;margin:5px 0}.duo-airfield-popup summary{cursor:pointer}.duo-airfield-popup button{padding:8px;background:#267d84;color:white;border:0;border-radius:7px}';map.getContainer().appendChild(style);
  function currentHtml(a,response){
    if(!response)return data.live?'Hover or click to load the current model report…':'Recorded mode: live airfield queries disabled.';
    if(response.error)return esc(response.error);
    const c=response.current,h=response.hourly,base=c.temperature_2m===null||c.dew_point_2m===null?null:Math.max(0,c.temperature_2m-c.dew_point_2m)*400;
    const rows=(h.time||[]).slice(1,7).map((t,i)=>{i++;return '<tr><td>'+esc(time(t))+'</td><td>'+val(h.wind_speed_10m?.[i])+' kt / '+val(h.wind_direction_10m?.[i],0)+'°</td><td>'+val(h.temperature_2m?.[i])+'°C</td><td>'+val(h.precipitation?.[i])+' mm/h</td></tr>';}).join('');
    return '<b>Open-Meteo current model estimate · '+esc(time(c.time))+'</b><br>Wind '+val(c.wind_speed_10m)+' kt from '+val(c.wind_direction_10m,0)+'° · gust '+val(c.wind_gusts_10m)+' kt<br>Temperature '+val(c.temperature_2m)+'°C · dewpoint '+val(c.dew_point_2m)+'°C<br>QNH estimate '+val(c.pressure_msl)+' hPa · visibility '+val(c.visibility===null?null:c.visibility/1000)+' km<br>Potential cloud base '+val(base,0)+' ft AGL / '+val(base===null||a.elevation_ft===null?null:base+a.elevation_ft,0)+' ft AMSL<small>Cloud base is a temperature/dewpoint estimate, not a measured ceiling.</small><details><summary>Six-hour airfield trend</summary><table><tr><th>Local time</th><th>Wind</th><th>Temp</th><th>Rain</th></tr>'+rows+'</table></details>';
  }
  function reportHtml(a){
    const own=data.reports[a.icao],station=own?.metar?a.icao:a.nearest,r=data.reports[station];
    const tafStation=own?.taf?a.icao:a.nearest_taf,t=data.reports[tafStation];
    return '<hr><b>NOAA METAR · '+esc(station)+(station===a.icao?'':' · nearby station '+a.nearest_km+' km away')+'</b><small>'+esc(r?.observed?time(r.observed):'No current report')+(r?.stale||!r?.observed||Date.now()-Date.parse(r.observed)>5400000?' · STALE / unavailable':'')+'</small><pre>'+esc(r?.metar||'METAR unavailable. Nearby reports do not describe this airfield exactly.')+'</pre><details><summary>Full TAF · '+esc(tafStation)+(tafStation===a.icao?'':' · '+a.nearest_taf_km+' km away')+'</summary><small>'+esc(t?.taf_from?time(t.taf_from)+' → '+time(t.taf_to):'No validity available')+(t?.taf_valid&&Date.now()<Date.parse(t.taf_to)?'':' · NOT CURRENT / unavailable')+'</small><pre>'+esc(t?.taf||'TAF unavailable')+'</pre></details>';
  }
  function markup(a,response){
    const runway=a.runways.map(r=>esc(r.ends)+' · '+(r.length_m===null?'length unavailable':r.length_m+' m')+' · '+esc(r.surface||'surface unknown')).join('<br>')||'Runway information unavailable';
    return '<b>✈ '+esc(a.label)+'</b><br>'+esc(a.icao||a.ident)+' · elevation '+val(a.elevation_ft,0)+' ft AMSL<br>'+runway+'<small>'+esc(a.status||'Runway availability and landing permission require an operator/AIP check.')+'</small><hr>'+currentHtml(a,response)+reportHtml(a);
  }
  async function fetchSurface(a){
    if(!data.live)return null;
    const old=cache.get(a.ident);if(old&&Date.now()-old.at<300000)return old.body;
    if(pending.has(a.ident))return pending.get(a.ident);
    const promise=(async()=>{const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),12000);try{
      const fields='temperature_2m,dew_point_2m,pressure_msl,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m';
      const params=new URLSearchParams({latitude:a.latitude,longitude:a.longitude,current:fields,hourly:fields+',precipitation',forecast_hours:7,wind_speed_unit:'kn',timezone:'GMT',timeformat:'unixtime',cell_selection:'nearest'});
      const response=await fetch('https://api.open-meteo.com/v1/forecast?'+params,{signal:abort.signal});if(!response.ok)throw Error('HTTP '+response.status);
      const body=await response.json();if(!body.current||!body.hourly||body.current_units?.wind_speed_10m!=='kn'||body.current_units?.visibility!=='m')throw Error('Invalid surface weather units');
      cache.set(a.ident,{at:Date.now(),body});return body;
    }catch(e){return {error:'Current model report unavailable: '+e.message};}finally{clearTimeout(timer);pending.delete(a.ident);}})();pending.set(a.ident,promise);return promise;
  }
  for(const a of data.airfields){
    const colour=a.kind==='former_gliding_airfield'?'#727c84':a.kind==='gliding_airfield'?'#007e88':a.metar?'#743fa1':'#195b9b';
    const marker=L.circleMarker([a.latitude,a.longitude],{radius:a.kind==='gliding_airfield'?7:5,weight:2,color:'#fff',fillColor:colour,fillOpacity:1,airfieldId:a.ident,bubblingMouseEvents:false}).addTo(group);
    marker.bindTooltip('✈ '+esc(a.label)+' · '+esc(a.icao||a.ident)+' · '+val(a.elevation_ft,0)+' ft AMSL. Hover for wind; click for METAR/TAF and six-hour model trend.',{sticky:true});
    marker.bindPopup(()=>{const el=document.createElement('div');el.innerHTML=markup(a,cache.get(a.ident)?.body);return el;},{className:'duo-airfield-popup',maxWidth:430,maxHeight:430});
    let hoverTimer;
    function refresh(body){
      const r=data.reports[a.icao]||data.reports[a.nearest],w=r?.wind;
      marker.setTooltipContent('<b>✈ '+esc(a.label)+'</b><br>'+val(a.elevation_ft,0)+' ft AMSL · '+a.runways.map(r=>esc(r.ends)).join(', ')+(w?'<br>METAR '+esc(a.icao||a.nearest)+': '+val(w.speed)+' kt / '+val(w.direction,0)+'° · gust '+val(w.gust)+' kt'+(r.stale?' · STALE':''):'')+'<br>'+currentHtml(a,body)+'<br>Click for full METAR/TAF and runway details.');
      if(marker.isPopupOpen()){const el=document.createElement('div');el.innerHTML=markup(a,body);marker.setPopupContent(el);el.querySelectorAll('details').forEach(d=>d.ontoggle=()=>marker.getPopup().update());}
    }
    marker.on('mouseover',()=>{hoverTimer=setTimeout(()=>fetchSurface(a).then(refresh),300);});marker.on('mouseout',()=>clearTimeout(hoverTimer));
    marker.on('click',()=>{fetchSurface(a).then(refresh);if(window.duoMapEvent)window.duoMapEvent(data.route?'airfield':'airfield_report',a.ident,marker.getLatLng());});
    if(data.selected===a.ident){marker.openPopup();fetchSurface(a).then(refresh);}
  }
  L.control.layers(null,{'✈ UK airfields · hover for live weather':group},{collapsed:true,position:'topright'}).addTo(map);
}
