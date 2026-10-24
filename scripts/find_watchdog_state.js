'use strict';

// Discovery is best effort. Download/integrity failures also fall back to an
// empty SQLite ledger in watchdog_state.py; no previous run can lock out a job.
module.exports = async function findState({github, context, core, recovery = process.env.RECOVERY_RUN_ID}) {
  const repo = context.repo, current = context.runId;
  const branch = context.payload.repository.default_branch;
  core.setOutput('run_id', '');
  core.setOutput('artifact_id', '');
  try {
    const runs = await github.paginate(github.rest.actions.listWorkflowRuns, {
      ...repo, workflow_id: 'soaring_watchdog.yml', branch, per_page: 100
    });
    let candidates = runs.filter(r => r.head_branch === branch);
    if (!candidates.some(r => r.id === current)) candidates.push({id: current});
    if (recovery) {
      const requested = candidates.find(r => String(r.id) === recovery);
      if (requested) candidates = [requested];
      else core.warning('Recovery run is not in this workflow/default branch; searching available state instead.');
    }
    // The artifact endpoint searches all retained runs, including failed jobs
    // and older attempts. A later run without a backup does not hide them.
    const eligible = new Set(candidates.map(r => r.id));
    const artifacts = await github.paginate(github.rest.actions.listArtifacts, {
      ...repo, name: 'soaring-watchdog-state', per_page: 100
    });
    const state = artifacts.filter(a => a.name === 'soaring-watchdog-state' &&
      !a.expired && eligible.has(a.workflow_run?.id))
      .sort((a,b) => Date.parse(b.created_at)-Date.parse(a.created_at))[0];
    if (!state) {
      core.warning('No previous alert-state artifact is available. Initializing empty state {}; earlier alerts may repeat.');
      return;
    }
    core.info(`Attempting alert-state artifact ${state.id} from run ${state.workflow_run.id}; integrity will be checked after download.`);
    core.setOutput('run_id', String(state.workflow_run.id));
    core.setOutput('artifact_id', String(state.id));
  } catch (error) {
    // Do not print request URLs/headers or tokens from SDK errors.
    core.warning(`Alert-state discovery unavailable (${error.status || error.name || 'API error'}). Initializing empty state {}; earlier alerts may repeat.`);
  }
};
