/* Canvas glyphs in CSS pixels. Barbs point into the wind; arrows follow it. */
function installDuoWindCanvas(map,options){
 'use strict';
 const style=Object.freeze(options);
 function stroke(c,width){
  c.lineCap='round';c.lineJoin='round';
  c.lineWidth=width+2*style.haloWidth;c.strokeStyle=style.halo;c.stroke();
  c.lineWidth=width;c.strokeStyle=style.colour;c.stroke();
 }
 function polygon(c,points){
  c.beginPath();c.moveTo(...points[0]);points.slice(1).forEach(p=>c.lineTo(...p));c.closePath();
  c.lineJoin='round';c.lineWidth=2*style.haloWidth;c.strokeStyle=style.halo;c.stroke();
  c.fillStyle=style.colour;c.fill();
 }
 function arrow(c,points){
  if(points.length<2)return null;
  const tip=points.at(-1);let previous=points.at(-2),i=points.length-2;
  while(i>0&&Math.hypot(tip[0]-previous[0],tip[1]-previous[1])<.01)previous=points[--i];
  const length=Math.hypot(tip[0]-previous[0],tip[1]-previous[1]);if(length<.01)return null;
  const dx=(tip[0]-previous[0])/length,dy=(tip[1]-previous[1])/length;
  c.beginPath();c.moveTo(...points[0]);points.slice(1).forEach(p=>c.lineTo(...p));stroke(c,style.arrowWidth);
  const base=[tip[0]-dx*style.headLength,tip[1]-dy*style.headLength],half=style.headWidth/2;
  const head=[tip,[base[0]-dy*half,base[1]+dx*half],[base[0]+dy*half,base[1]-dx*half]];
  polygon(c,head);return {tip,head};
 }
 function barbParts(knots){
  if(!Number.isFinite(knots)||knots<0)return null;
  const rounded=Math.round(knots/5)*5,pennants=Math.floor(rounded/50),full=Math.floor((rounded%50)/10),half=rounded%10>=5?1:0;
  return {rounded,pennants,full,half,calm:rounded===0,shaft:Math.max(30,10+pennants*11+(full+half)*6)};
 }
 function barb(c,x,y,u,v){
  const knots=Math.hypot(u,v)*1.943844492,parts=barbParts(knots);
  if(!parts)return null;
  c.save();c.translate(x,y);
  // (u,v) are east/north components. Canvas y grows southwards.
  const fromAngle=Math.atan2(v,-u);c.rotate(fromAngle);
  if(parts.calm){c.beginPath();c.arc(0,0,4,0,2*Math.PI);stroke(c,style.barbWidth);}
  else{
   c.beginPath();c.moveTo(0,0);c.lineTo(parts.shaft,0);stroke(c,style.barbWidth);
   let cursor=parts.shaft;
   for(let i=0;i<parts.pennants;i++){polygon(c,[[cursor,0],[cursor-9,11],[cursor-9,0]]);cursor-=11;}
   if(parts.half&&!parts.full&&!parts.pennants)cursor-=6;
   c.beginPath();
   for(let i=0;i<parts.full;i++){c.moveTo(cursor,0);c.lineTo(cursor-5,11);cursor-=6;}
   if(parts.half){c.moveTo(cursor,0);c.lineTo(cursor-2.5,5.5);}
   stroke(c,style.barbWidth);
  }
  c.restore();
  const label=Math.round(knots)+' kt';c.font='bold 11px Arial';c.textAlign='center';c.textBaseline='middle';
  c.lineWidth=3;c.strokeStyle=style.halo;c.strokeText(label,x,y+parts.shaft+14);
  c.fillStyle=style.colour;c.fillText(label,x,y+parts.shaft+14);
  return {knots,parts,fromAngle,x,y};
 }
 map._duoWindGlyphs={style,arrow,barb,barbParts};
}
