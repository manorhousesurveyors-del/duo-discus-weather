function installDuoBridge(map, identity) {
  'use strict';
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
    window.Streamlit.setComponentValue(payload);
    });
  };
}
