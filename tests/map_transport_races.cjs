// Execute the production iframe script with controlled asynchronous decoding.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const zlib=require('node:zlib');
const source=fs.readFileSync(process.argv[2]||'map_component/index.html','utf8').match(/<script>([\s\S]*)<\/script>/)[1];
const messages=[],handlers={},wrapper={innerHTML:''};let gate=null;
const parent={location:{origin:'https://local.test'},postMessage:message=>messages.push(message)};
const window={location:parent.location,addEventListener:(name,handler)=>handlers[name]=handler};
const document={createElement:()=>({remove(){}}),head:{append:element=>queueMicrotask(()=>element.onload())},getElementById:()=>wrapper};
const NativeResponse=Response;
class DelayedResponse extends NativeResponse{
 async text(){const waiting=gate;gate=null;if(waiting)await waiting.promise;return super.text();}
}
vm.runInNewContext(source,{window,parent,document,console,performance,Blob,Response:DelayedResponse,DecompressionStream,atob,Uint8Array,Date,Map,Set});
const message=args=>handlers.message({source:parent,data:{type:'streamlit:render',args}});
const id=name=>name.repeat(64);
const documentFor=name=>({html:'<div></div>',header:'',script:`window.rendered='${name}';window.map_div={on(){},remove(){},_duoAcceptTask(task){window.task=task;}};`});
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};}
const compressed=name=>({document_id:id(name),compressed_document:zlib.deflateSync(JSON.stringify(documentFor(name))).toString('base64'),canonical_task:{version:2}});
(async()=>{
 await message({document_id:id('a'),...documentFor('a'),canonical_task:{version:1}});
 assert.equal(window.rendered,'a');
 const first=deferred();gate=first;const rendering=message(compressed('b'));
 await new Promise(setImmediate);
 await message({document_id:id('a'),canonical_task:{version:1}}); // late old-map ACK
 await message({document_id:id('b'),canonical_task:{version:3}}); // new-map fragment
 assert.equal(messages.filter(m=>m.value?.document_request).length,0,'Do not request a document already decoding');
 first.resolve();await rendering;
 assert.equal(window.rendered,'b','Late ACK must not cancel a selected layer update');
 assert.equal(window.task.version,3,'Keep the newest canonical task while decoding');
 // A real replacement still supersedes a pending full document.
 const second=deferred();gate=second;const stale=message(compressed('c'));
 await new Promise(setImmediate);
 await message({document_id:id('d'),...documentFor('d'),canonical_task:{version:4}});
 second.resolve();await stale;assert.equal(window.rendered,'d');
 await message({document_id:id('e'),canonical_task:{version:5}});
 await message({document_id:id('e'),canonical_task:{version:5}});
 assert.equal(messages.filter(m=>m.value?.document_request===id('e')).length,1,'Request an absent document once');
 console.log('Map decoding races and canonical task handoff passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
