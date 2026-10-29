const assert = require('node:assert/strict');
const findState = require('../scripts/find_watchdog_state.js');

async function scenario({runs=[],artifacts=[],recovery='',failure=false,hang=false}) {
  const outputs={}, warnings=[], calls=[];
  const github={rest:{actions:{
    listArtifacts:async args=>{
      calls.push(args);
      if(hang)return new Promise(()=>{});
      if(failure)throw Object.assign(new Error('private SDK details'),{status:403});
      return {data:{artifacts}};
    },
    getWorkflowRun:async args=>({data:runs.find(r=>r.id===args.run_id)||{}})
  }}};
  await findState({github,context:{repo:{owner:'test',repo:'test'},runId:3,payload:{repository:{default_branch:'main'}}},
    core:{setOutput:(k,v)=>outputs[k]=v,warning:m=>warnings.push(m),info:()=>{}},recovery,budgetMs:hang?20:6000});
  assert.equal(calls[0].name,'soaring-watchdog-state');assert.equal(calls[0].request.timeout,3000);
  return {outputs,warnings};
}
const run=id=>({id,head_branch:'main',path:'.github/workflows/soaring_watchdog.yml',conclusion:'failure'});
const artifact=(id,runId,created,extra={})=>({id,name:'soaring-watchdog-state',workflow_run:{id:runId},created_at:created,expired:false,...extra});

(async()=>{
  const recovered=await scenario({runs:[run(3),run(2),run(1)],artifacts:[artifact(10,1,'2026-10-24T10:00:00Z')]});
  assert.equal(recovered.outputs.run_id,'1');assert.equal(recovered.outputs.artifact_id,'10');
  const rerun=await scenario({runs:[run(3),run(2),run(1)],artifacts:[artifact(10,1,'2026-10-24T10:00:00Z'),artifact(30,3,'2026-10-24T12:00:00Z')]});
  assert.equal(rerun.outputs.run_id,'3');
  for(const options of [
    {runs:[run(1),run(2)]},
    {runs:[run(1)],artifacts:[artifact(10,1,'2026-10-24',{expired:true})]},
    {runs:[run(1)],artifacts:[artifact(99,99,'2026-10-24')]},
    {runs:[{...run(1),head_branch:'feature'}],artifacts:[artifact(10,1,'2026-10-24')]},
    {runs:[{...run(1),path:'.github/workflows/tests.yml'}],artifacts:[artifact(10,1,'2026-10-24')]},
    {failure:true}, {hang:true}
  ]){
    const result=await scenario(options);
    assert.equal(result.outputs.run_id,'');assert.equal(result.outputs.artifact_id,'');
    assert.ok(result.warnings.some(w=>w.includes('empty state {}')));
    assert.ok(result.warnings.every(w=>!w.includes('private SDK details')));
  }
  const badOverride=await scenario({runs:[run(1)],artifacts:[artifact(10,1,'2026-10-24')],recovery:'not-a-run'});
  assert.equal(badOverride.outputs.run_id,'1');
  const override=await scenario({runs:[run(1),run(2)],artifacts:[artifact(10,1,'2026-10-23'),artifact(20,2,'2026-10-24')],recovery:'1'});
  assert.equal(override.outputs.run_id,'1');
  // Same-named artifacts from another workflow must not hide the watchdog ledger.
  const foreign=await scenario({runs:[run(1),{...run(2),path:'.github/workflows/tests.yml'}],artifacts:[artifact(10,1,'2026-10-23'),artifact(20,2,'2026-10-24')]});
  assert.equal(foreign.outputs.run_id,'1');
  console.log('State discovery: failed-run artifact, rerun, empty, expired, foreign, API failure, bounded timeout and recovery override passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
