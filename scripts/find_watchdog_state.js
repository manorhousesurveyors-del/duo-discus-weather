'use strict';

const STATE_ARTIFACT = 'soaring-watchdog-state';
const WORKFLOW = '.github/workflows/soaring_watchdog.yml';

// Search retained artifacts first; do not enumerate the workflow's whole history.
// Verify provenance with the run endpoint before accepting a candidate. Failed
// runs and earlier attempts may contain the most recent valid delivery ledger.
module.exports = async function findState({github, context, core, recovery = process.env.RECOVERY_RUN_ID, budgetMs = 6000}) {
  const repo = context.repo, branch = context.payload.repository.default_branch;
  core.setOutput('run_id', '');
  core.setOutput('artifact_id', '');
  const controller = new AbortController();
  const request = {timeout: 3000, signal: controller.signal};
  let timer;
  async function discover() {
    let artifacts = [];
    // Thirty days at four runs/day fits in two pages. Three pages also cover
    // manual tests, without an unbounded history scan on every six-hour run.
    for (let page = 1; page <= 3; page++) {
      const response = await github.rest.actions.listArtifacts({
        ...repo, name: STATE_ARTIFACT, per_page: 100, page, request
      });
      artifacts.push(...response.data.artifacts);
      if (response.data.artifacts.length < 100) break;
    }
    const candidates = artifacts.filter(a => a.name === STATE_ARTIFACT && !a.expired && a.workflow_run?.id)
      .sort((a,b) => Date.parse(b.created_at)-Date.parse(a.created_at));
    if (recovery) candidates.sort((a,b) => Number(String(b.workflow_run.id)===recovery)-Number(String(a.workflow_run.id)===recovery));
    for (const state of candidates.slice(0, 8)) {
      if (controller.signal.aborted) return null;
      const {data: run} = await github.rest.actions.getWorkflowRun({...repo, run_id: state.workflow_run.id, request});
      if (run.head_branch === branch && run.path?.split('@')[0] === WORKFLOW) return state;
    }
    return null;
  }
  try {
    const deadline = new Promise((_, reject) => {timer = setTimeout(() => {
      controller.abort(); reject(new Error('State discovery deadline'));
    }, budgetMs);});
    const state = await Promise.race([discover(), deadline]);
    if (!state) {
      core.warning('No previous alert-state artifact is available. Initializing empty state {}; earlier alerts may repeat.');
      return;
    }
    core.info(`Attempting alert-state artifact ${state.id} from run ${state.workflow_run.id}; integrity will be checked after download.`);
    core.setOutput('run_id', String(state.workflow_run.id));
    core.setOutput('artifact_id', String(state.id));
  } catch (error) {
    core.warning(`Alert-state discovery unavailable (${error.status || error.name || 'API error'}). Initializing empty state {}; earlier alerts may repeat.`);
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
};
