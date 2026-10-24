function installDuoCanvasAPI(map) {
 'use strict';
 map.on('layeradd',e=>{const l=e.layer;if(!(l instanceof L.TileLayer)||l._duoFallbackSet)return;l._duoFallbackSet=true;
  if(!/server\.arcgisonline|basemaps\.cartocdn/.test(l._url))return;let failures=0;l.on('tileerror',()=>{if(++failures!==3)return;
   if(/World_Shaded_Relief/.test(l._url)){if(map.hasLayer(l))map.removeLayer(l);return;}
   l.setUrl('https://tile.openstreetmap.org/{z}/{x}/{y}.png');map.attributionControl?.addAttribution('Fallback © OpenStreetMap contributors');});});
 window.duoEsc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 window.duoRegisterLayer=(m,name,layer)=>{if(m._duoLayers)m._duoLayers.addOverlay(layer,name);else(m._duoPendingLayers??=[]).push([name,layer]);};
 if(window.DuoCanvasPoints)return;
 window.DuoCanvasPoints=L.Layer.extend({
  initialize(records,options={}){this.records=records;this.options=options;this.visible=[];this.hits=new Map();this.frame=0;},
  onAdd(m){this._map=m;this.canvas=L.DomUtil.create('canvas','duo-points-canvas leaflet-zoom-hide',m.getPane('markerPane'));this.canvas.style.pointerEvents='none';
   m.on('moveend zoomend resize',this.schedule,this);m.on('mousemove',this.hover,this);m.on('click',this.click,this);this.draw();},
  onRemove(m){cancelAnimationFrame(this.frame);m.off('moveend zoomend resize',this.schedule,this);m.off('mousemove',this.hover,this);m.off('click',this.click,this);this.canvas.remove();if(this.tip)m.removeLayer(this.tip);},
  schedule(){cancelAnimationFrame(this.frame);this.frame=requestAnimationFrame(()=>this.draw());},
  draw(){if(!this._map||!this.canvas)return;const start=performance.now(),m=this._map,size=m.getSize(),ratio=Math.min(2,window.devicePixelRatio||1),canvas=this.canvas;
   canvas.width=size.x*ratio;canvas.height=size.y*ratio;canvas.style.width=size.x+'px';canvas.style.height=size.y+'px';L.DomUtil.setPosition(canvas,m.containerPointToLayerPoint([0,0]));
   const c=canvas.getContext('2d');c.scale(ratio,ratio);c.font='12px Arial';this.hits.clear();this.visible=[];
   const bounds=m.getBounds().pad(.05),labels=new Set();
   for(const p of this.records){if(!bounds.contains([p.lat,p.lon]))continue;const xy=m.latLngToContainerPoint([p.lat,p.lon]);if(xy.x< -12||xy.y< -12||xy.x>size.x+12||xy.y>size.y+12)continue;
    if(this.options.drawPoint)this.options.drawPoint(c,p,xy.x,xy.y);
    else{const r=p.kind==='airfield'?5:3.6;c.beginPath();c.arc(xy.x,xy.y,r,0,2*Math.PI);c.fillStyle=p.colour||'#305873';c.fill();c.strokeStyle='#fff';c.lineWidth=1.3;c.stroke();}
    const hit={p,x:xy.x,y:xy.y};this.visible.push(hit);const key=Math.floor(xy.x/24)+':'+Math.floor(xy.y/24);if(!this.hits.has(key))this.hits.set(key,[]);this.hits.get(key).push(hit);
    if(m.getZoom()>=9||p.priority){const cell=Math.floor(xy.x/110)+':'+Math.floor(xy.y/22);if(!labels.has(cell)){const label=p.label||p.code;c.lineWidth=3;c.strokeStyle='#ffffffe8';c.strokeText(label,xy.x+8,xy.y+4);c.fillStyle='#152a35';c.fillText(label,xy.x+8,xy.y+4);labels.add(cell);}}
   }
   this.lastDrawMs=performance.now()-start;
  },
  nearest(point){let best=null,dist=13;const x=Math.floor(point.x/24),y=Math.floor(point.y/24);for(let i=-1;i<=1;i++)for(let j=-1;j<=1;j++)for(const hit of this.hits.get((x+i)+':'+(y+j))||[]){const d=Math.hypot(hit.x-point.x,hit.y-point.y);if(d<dist){best=hit.p;dist=d;}}return best;},
  hover(e){const p=this.nearest(e.containerPoint);if(p===this.hovered)return;this.hovered=p;if(this.tip)this._map.removeLayer(this.tip);if(!p){this.options.onLeave?.();return;}
   this.tip=L.tooltip({direction:'top',offset:[0,-8],opacity:.97}).setLatLng([p.lat,p.lon]).setContent(this.options.tooltip?.(p)||duoEsc(p.name)).addTo(this._map);this.options.onHover?.(p,this.tip);},
  click(e){const p=this.nearest(e.containerPoint);if(!p)return;if(e.originalEvent){if(e.originalEvent._duoSelected)return;e.originalEvent._duoSelected=true;}
   this._map._duoLocalClickUntil=performance.now()+500;
   this.options.onClick?.(p,e);}
 });
}
