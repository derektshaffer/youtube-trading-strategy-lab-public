const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const {decrypt, destinationScope, assertRecoveriesAcknowledged, loadArtifactClient} = require('./index.cjs');
let stage = 'configuration';

function python(args) {
  const r = cp.spawnSync('python', args, {encoding: 'utf8', env: {...process.env, RETAIN_CLOUD_RECOVERY: '1'}});
  if (r.status !== 0) throw new Error('Fixture recovery process failed');
  // Recovery CLI only emits metadata/acknowledgement, but do not relay arbitrary output.
  return r.stdout;
}
async function main() {
  if (process.env.GITHUB_REF !== 'refs/heads/codex/cloud-backup-capacity-smoke') throw new Error('Wrong source ref');
  const source = process.env.FIXTURE_SOURCE_RUN;
  if (!/^[0-9]+$/.test(source) || source === process.env.GITHUB_RUN_ID) throw new Error('Earlier run required');
  const scope = destinationScope();
  stage = 'preflight_block';
  let blocked = false;
  try { await assertRecoveriesAcknowledged(); } catch { blocked = true; }
  if (!blocked) throw new Error('New worker did not block on earlier recovery');
  const wrapper = cp.spawnSync('node', [path.join(__dirname, 'index.cjs')], {encoding: 'utf8', env: process.env});
  if (wrapper.status !== 1 || !wrapper.stderr.includes('New research blocked:')) throw new Error('Wrapper did not block before computation');
  const [repositoryOwner, repositoryName] = process.env.GITHUB_REPOSITORY.split('/');
  const findBy = {token: process.env.RESEARCH_ACTIONS_TOKEN, workflowRunId: Number(source), repositoryOwner, repositoryName};
  stage = 'artifact_download';
  const client = await loadArtifactClient();
  const {artifacts} = await client.listArtifacts({findBy});
  const matches = artifacts.filter(a => a.name.startsWith(`research-recovery-v2-${scope}-`));
  if (matches.length !== 1) throw new Error('Expected exactly one source envelope');
  const artifact = matches[0];
  const digest = artifact.name.split('-').at(-1);
  if (!/^sha256:[a-f0-9]{64}$/.test(artifact.digest || '')) throw new Error('Artifact checksum missing');
  const directory = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP, 'fixture-download-'));
  const result = await client.downloadArtifact(artifact.id, {findBy, path: directory,
    expectedHash: artifact.digest});
  if (result.digestMismatch) throw new Error('Artifact download checksum mismatch');
  stage = 'artifact_decrypt';
  const sealed = fs.readFileSync(path.join(directory, `${digest}.tilrec`));
  const bundle = path.join(directory, 'recovered.json.gz');
  fs.writeFileSync(bundle, decrypt(sealed), {mode: 0o600, flag: 'wx'});
  stage = 'recovery_inspect';
  python(['recover_cloud_research.py', bundle, '--directory', path.join(process.env.RUNNER_TEMP, 'inspect-only')]);
  stage = 'recovery_sync';
  python(['recover_cloud_research.py', bundle, '--directory', path.join(process.env.RUNNER_TEMP, 'fixture-recovered'), '--sync']);
  stage = 'recovery_verify';
  python(['cloud_backup_capacity_smoke.py', 'verify-recovered']);
  const firstRecovery = JSON.parse(fs.readFileSync('capacity-smoke-output/recovered.json'));
  // Re-import the original immutable envelope on a fresh local store.
  stage = 'idempotent_reimport';
  python(['recover_cloud_research.py', bundle, '--directory', path.join(process.env.RUNNER_TEMP, 'fixture-idempotent'), '--sync']);
  python(['cloud_backup_capacity_smoke.py', 'verify-recovered']);
  if (JSON.parse(fs.readFileSync('capacity-smoke-output/recovered.json')).library_sha !== firstRecovery.library_sha) {
    throw new Error('Idempotent recovery changed the canonical fixture');
  }
  stage = 'receipt_preflight';
  await assertRecoveriesAcknowledged();
  stage = 'cas_race';
  python(['cloud_backup_capacity_smoke.py', 'race']);
  stage = 'final_preflight';
  await assertRecoveriesAcknowledged();
  fs.mkdirSync('capacity-smoke-output', {recursive: true});
  fs.writeFileSync('capacity-smoke-output/recovery.json', JSON.stringify({
    source_run_id: source, recovery_run_id: process.env.GITHUB_RUN_ID, source_artifact_id: artifact.id,
    delta_sha256: digest, new_worker_blocked: true, decrypted_and_recovered: true,
    reimport_idempotent: true, receipt_unblocks_preflight: true, real_cas_race_passed: true,
    production_writes: 0, research_executed: false,
  }, null, 2));
  console.log('PASS: prior-run block, authenticated artifact recovery, idempotence, receipts, and real CAS race.');
}
module.exports = {main, failureStage: () => stage};
