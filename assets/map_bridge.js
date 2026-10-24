function installDuoBridge(map, identity) {
  'use strict';
  map._duoClientClicks=['planner','wave','traffic'].includes(identity);
  // Leaflet focuses its tabindex container on mousedown. In a tall iframe,
  // default focus can scroll the parent page before mouseup and select a
  // different hill. Retain keyboard focus without moving the iframe.
  const container=map.getContainer(),focus=container.focus.bind(container);
  container.focus=options=>focus({...options,preventScroll:true});
  // Hidden tabs initially have zero width. Redraw only when visibility or size
  // changes; the timer is a fallback for browsers with delayed observer events.
  let lastSize='', resizeFrame=0;
  const resize=()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>{
    const width=container.offsetWidth,height=container.offsetHeight;
    if(!width||!height){lastSize='';return;}
    const size=width+':'+height;if(size===lastSize)return;lastSize=size;
    map.invalidateSize(true);map.fire('resize',{newSize:L.point(width,height)});
  });};
  let pendingPoint=null;
  map.on('click',e=>{const p=map._duoBga?.nearest(e.containerPoint,18)||map._duoAirfields?.nearest(e.containerPoint,18);pendingPoint={lat:p?.lat??e.latlng.lat,lon:p?.lon??e.latlng.lng,name:p?.name||'Clicked map location'};});
  const observer=new ResizeObserver(resize),visible=new IntersectionObserver(entries=>{
    if(entries.some(e=>e.isIntersecting))resize();
    // Publish the last selected location once when leaving a map, never on
    // mousemove or on every task click. The bot then sees that exact point.
    else if(pendingPoint&&window.duoMapEvent){window.duoMapEvent('briefing_point',JSON.stringify(pendingPoint),[pendingPoint.lat,pendingPoint.lon]);pendingPoint=null;}
  });
  observer.observe(container);visible.observe(container);const timer=setInterval(resize,500);
  document.addEventListener('visibilitychange',resize);resize();
  map.on('unload',()=>{observer.disconnect();visible.disconnect();clearInterval(timer);cancelAnimationFrame(resizeFrame);document.removeEventListener('visibilitychange',resize);});
  // Native component click handlers are installed after map construction.
  // Filter their outgoing values, keeping only deliberate commits/callouts.
  const rawSend=window._duoRawSend||(window._duoRawSend=window.Streamlit.setComponentValue.bind(window.Streamlit));
  window.Streamlit.setComponentValue=value=>{if(map._duoSending||(!map._duoClientClicks&&performance.now()>(map._duoLocalClickUntil||0)))rawSend(value);};
  // Preserve the view in the browser. Panning/zooming never changes either
  // returned click object and therefore never causes a Python rerun.
  const key='duo-map-view:'+identity;
  try { const saved=JSON.parse(sessionStorage.getItem(key)||'null');if(saved)map.setView(saved.centre,saved.zoom,{animate:false}); } catch(e) {}
  map.on('moveend',()=>{try{const c=map.getCenter();sessionStorage.setItem(key,JSON.stringify({centre:[c.lat,c.lng],zoom:map.getZoom()}));}catch(e){}});
  window.duoMapEvent=(kind,value,coordinate)=>{
    const state=window.__GLOBAL_DATA__;if(!state)return;
    const point=L.latLng(coordinate||map.getCenter()).wrap();
    const object={lat:point.lat,lng:point.lng,duo:{kind,value:String(value),nonce:Date.now()+':'+Math.random()}};
    queueMicrotask(()=>{
    state.last_object_clicked=object;
    const payload={last_clicked:state.lat_lng_clicked||null,last_object_clicked:object};
    state.previous_data=payload;
    map._duoSending=true;
    try{window.Streamlit.setComponentValue(payload);}finally{map._duoSending=false;}
    });
  };
}
