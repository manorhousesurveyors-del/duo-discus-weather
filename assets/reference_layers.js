/* Prominent reference-layer switches; no Streamlit message or rerun. */
function installDuoReferenceControls(map,data){
 const control=L.control({position:'topright'});
 control.onAdd=()=>{
  const el=L.DomUtil.create('div','duo-reference-switches');
  el.style.cssText='background:#172532ed;color:white;padding:10px 14px;border-radius:12px;font:14px/1.6 Arial;box-shadow:0 2px 12px #0004';
  el.innerHTML='<label title="Show or hide BGA turnpoints instantly. Off keeps wind and aircraft labels clear."><input type="checkbox" data-reference="bga"> Show BGA Turnpoints</label>'+(data.traffic?'<br><label title="Show or hide airfields instantly. Click a visible airfield for its details."><input type="checkbox" data-reference="airfields"> Show Airfields</label>':'');
  L.DomEvent.disableClickPropagation(el);L.DomEvent.disableScrollPropagation(el);return el;
 };
 control.addTo(map);
 const layers={bga:map._duoBga,airfields:map._duoAirfields};
 function sync(){control.getContainer().querySelectorAll('input').forEach(input=>{input.checked=!!layers[input.dataset.reference]&&map.hasLayer(layers[input.dataset.reference]);});}
 control.getContainer().querySelectorAll('input').forEach(input=>{const layer=layers[input.dataset.reference];input.disabled=!layer;input.onchange=()=>{if(input.checked)map.addLayer(layer);else map.removeLayer(layer);};});
 map.on('layeradd layerremove',sync);sync();
 map.on('unload',()=>{map.off('layeradd layerremove',sync);control.remove();});
}
