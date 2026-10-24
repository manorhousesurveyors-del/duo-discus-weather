const assert = require('node:assert/strict');
const findState = require('../scripts/find_watchdog_state.js');

async function scenario({runs=[],artifacts=[],recovery='',failure=false}) {
  const outputs={}, warnings=[];
  const github={rest:{actions:{listWorkflowRuns:'runs',listArtifacts:'artifacts'}},
    paginate:async method=>{if(failure)throw Object.assign(new Error('private SDK details'),{status:403});return method==='runs'?runs:artifacts;}};
  await findState({github,context:{repo:{owner:'test',repo:'test'},runId:3,payload:{repository:{default_branch:'main'}}},
    core:{setOutput:(k,v)=>outputs[k]=v,warning:m=>warnings.push(m),info:()=>{}},recovery});
  return {outputs,warnings};
}
const run=id=>({id,head_branch:'main',conclusion:'failure'});
const artifact=(id,runId,created,extra={})=>({id,name:'soaring-watchdog-state',workflow_run:{id:runId},created_at:created,expired:false,...extra});

(async()=>{
  // Run #1 uploaded before failing; #2 failed without backup. #3 must find #1.
  const recovered=await scenario({runs:[run(3),run(2),run(1)],artifacts:[artifact(10,1,'2026-10-24T10:00:00Z')]});
  assert.equal(recovered.outputs.run_id,'1');assert.equal(recovered.outputs.artifact_id,'10');
  const rerun=await scenario({runs:[run(3),run(2),run(1)],artifacts:[artifact(10,1,'2026-10-24T10:00:00Z'),artifact(30,3,'2026-10-24T12:00:00Z')]});
  assert.equal(rerun.outputs.run_id,'3');
  for(const options of [
    {runs:[run(1),run(2)]},
    {runs:[run(1)],artifacts:[artifact(10,1,'2026-10-24',{expired:true})]},
    {runs:[run(1)],artifacts:[artifact(99,99,'2026-10-24')]},
    {runs:[{id:1,head_branch:'feature'}],artifacts:[artifact(10,1,'2026-10-24')]},
    {failure:true},
  ]){
    const result=await scenario(options);
    assert.equal(result.outputs.run_id,'');assert.equal(result.outputs.artifact_id,'');
    assert.ok(result.warnings.some(w=>w.includes('empty state {}')));
    assert.ok(result.warnings.every(w=>!w.includes('private SDK details')));
  }
  const badOverride=await scenario({runs:[run(1)],artifacts:[artifact(10,1,'2026-10-24')],recovery:'not-a-run'});
  assert.equal(badOverride.outputs.run_id,'1');assert.ok(badOverride.warnings.length);
  const override=await scenario({runs:[run(1),run(2)],artifacts:[artifact(10,1,'2026-10-23'),artifact(20,2,'2026-10-24')],recovery:'1'});
  assert.equal(override.outputs.run_id,'1');
  console.log('State discovery: older failed-run artifact, rerun, empty, expired, foreign, API failure and recovery override passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
