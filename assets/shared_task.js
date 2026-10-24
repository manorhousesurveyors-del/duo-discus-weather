function installDuoSharedTask(map,initial){
 'use strict';
 const KEY='duo_active_task_v1',esc=window.duoEsc;let state=initial;const listeners=[];
 function valid(s){return s&&s.session===initial.session&&s.date===initial.date&&Number.isSafeInteger(s.version)&&Array.isArray(s.points)&&s.points.length<=20&&s.points.every(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));}
 try{const saved=JSON.parse(localStorage.getItem(KEY)||'null');if(valid(saved)&&saved.version>state.version)state=saved;}catch(e){}
 function store(){try{if(state.points.length)localStorage.setItem(KEY,JSON.stringify(state));else localStorage.removeItem(KEY);}catch(e){}}
 const route=L.layerGroup().addTo(map);
 function draw(){if(initial.editable)return;route.clearLayers();const points=state.points;if(points.length>1)L.polyline(points.map(p=>[p.lat,p.lon]),{color:'#223c58',weight:4,interactive:false}).addTo(route);
  points.forEach((p,i)=>{L.circleMarker([p.lat,p.lon],{radius:7,color:'#17394e',fill:true,fillOpacity:1}).bindTooltip((i===0?'Start':i===points.length-1?'Finish':'Turnpoint')+' '+(i+1)+': '+esc(p.code)+' · '+esc(p.name)).addTo(route);});
 }
 function notify(){store();draw();listeners.forEach(fn=>fn(state));}
 function send(action){if(window.duoMapEvent)window.duoMapEvent('task_sync',JSON.stringify({...state,action,briefing_point:map._duoBriefingPoint||null}));}
 function update(points,complete,action){state={...state,points:points.map(p=>({...p})),complete,version:state.version+1};notify();send(action);}
 function accept(next){if(!valid(next)||next.version<state.version)return;state={...next,editable:initial.editable};notify();}
 map._duoShared={get state(){return state;},update,accept,subscribe:fn=>listeners.push(fn),route};
 map._duoAcceptTask=accept;
 const storage=e=>{if(e.key!==KEY)return;if(e.newValue){try{accept(JSON.parse(e.newValue));}catch(err){}}else if(state.points.length){state={...state,points:[],complete:false,version:state.version+1};draw();listeners.forEach(fn=>fn(state));}};
 window.addEventListener('storage',storage);map.on('unload',()=>window.removeEventListener('storage',storage));
 if(!initial.editable){const toolbar=L.control({position:'bottomleft'});toolbar.onAdd=()=>{const el=L.DomUtil.create('div');el.innerHTML='<button data-clear-task title="Clear the shared route from Weather and Task maps immediately." style="background:#17394e;color:white;border:0;border-radius:10px;padding:10px 16px;cursor:pointer">Clear Task</button>';L.DomEvent.disableClickPropagation(el);el.querySelector('button').onclick=()=>update([],false,'clear');return el;};toolbar.addTo(map);}
 notify();
 if(state.version>initial.version)queueMicrotask(()=>send('recover'));
}
