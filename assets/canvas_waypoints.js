function installDuoWaypoints(map,data){
 const layer=new DuoCanvasPoints(data.points,{tooltip:p=>duoEsc(p.title)+'<br>'+ (p.elevation_m===null?'Elevation unavailable':Math.round(p.elevation_m*3.28084)+' ft AMSL')+'. Click to add to your draft task.',
  onClick:(p)=>{if(map._duoTask)map._duoTask.add(p);else if(map._duoWave)map._duoWave.sectionAt(L.latLng(p.lat,p.lon));else if(window.duoMapEvent)window.duoMapEvent('point',p.code,[p.lat,p.lon]);}});
 map._duoBga=layer;if(data.show)layer.addTo(map);duoRegisterLayer(map,'BGA turnpoints · canvas',layer);
}
