/* Explicitly synthetic cloud profiles for the real classifier and canvas. */
const heights=[2000,2800,3600,5000,6500,10000,14000,18000,23500,30000,34000,39000];
const pressures=[950,925,900,850,800,700,600,500,400,300,250,200];
const temps=[12,10,8,6,4,-2,-10,-18,-28,-40,-48,-55];
function scene(changes={}){
 const row={top:3500,lcl:5000,climb:0,cloud_low:0,cloud_mid:0,cloud_high:0,cloud_total:0,cape:0,lifted_index:2,rain:0,showers:0,
  levels:pressures.map((p,i)=>[heights[i],65,10,0,0,temps[i],temps[i]-6,90,p]),...changes};
 row.profile=[[100,65,10,0,0,15,9,70,null],...row.levels];return row;
}
const setRH=(r,pressures,rh)=>{r.levels.forEach(p=>{if(pressures.includes(p[8])){p[1]=rh;p[6]=p[5]-1;}});return r;};
function scenes(){
 const spread=setRH(scene({top:6500,lcl:5000,climb:3,cloud_low:65}),[850,800],88);spread.levels.find(p=>p[8]===800)[5]=9;
 const cb=setRH(scene({top:11000,lcl:4000,climb:4,cape:650,rain:1.2,showers:.8}),[850,800,700,600,500,400,300,250],90);
 const ice=scene({cloud_high:60});ice.levels.forEach(p=>{if(p[8]<=400)p[7]=115;});
 const missing=scene({top:null,lcl:null,climb:null,cloud_low:null,cloud_mid:null,cloud_high:null,cape:null,lifted_index:null,rain:null,showers:null});missing.levels=missing.levels.map(p=>[p[0],null,null,null,null,null,null,null,p[8]]);missing.profile=missing.levels;
 return {humilis:scene({top:5500,climb:2}),mediocris:scene({top:6500,climb:2.5}),congestus:scene({top:13500,climb:3.5}),
  spread,storm:cb,stratus:scene({cloud_mid:90,cloud_low:85,rain:.8}),cirrus:ice,clear:scene(),missing};
}
module.exports={scene,scenes,setRH};
