const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const MAGIC = Buffer.from('TILREC01');
let operation = 'startup';

function safeFailure(error) {
  const message = String(error?.message || '');
  const categories = [
    ['runtime_configuration', /ACTIONS_RUNTIME|ACTIONS_RESULTS|Unable to get.*(?:token|URL)|runtime token/i],
    ['dependency', /Cannot find module|ERR_REQUIRE|not a function|not a constructor/i],
    ['authorization', /unauthorized|forbidden|permission|401|403/i],
    ['capacity', /quota|storage limit|too large|artifact limit/i],
    ['network', /fetch failed|timeout|timed out|ECONN|ENOTFOUND/i],
  ];
  return {stage: operation, category: categories.find(([, pattern]) => pattern.test(message))?.[0] || 'unclassified'};
}

function key(salt) {
  const secret = process.env.GITHUB_BACKUP_TOKEN;
  if (!secret) throw new Error('Recovery encryption key unavailable');
  return crypto.scryptSync(secret, salt, 32);
}
function encrypt(raw) {
  const salt = crypto.randomBytes(16), iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key(salt), iv);
  cipher.setAAD(MAGIC);
  const body = Buffer.concat([cipher.update(raw), cipher.final()]);
  return Buffer.concat([MAGIC, salt, iv, cipher.getAuthTag(), body]);
}
function decrypt(raw) {
  if (!raw.subarray(0, 8).equals(MAGIC)) throw new Error('Invalid recovery format');
  const cipher = crypto.createDecipheriv('aes-256-gcm', key(raw.subarray(8, 24)), raw.subarray(24, 36));
  cipher.setAAD(MAGIC);
  cipher.setAuthTag(raw.subarray(36, 52));
  return Buffer.concat([cipher.update(raw.subarray(52)), cipher.final()]);
}

async function requestJSON(url, token, missingOK = false) {
  const response = await fetch(url, {headers: {
    Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
  }, signal: AbortSignal.timeout(60000)});
  if (missingOK && response.status === 404) return null;
  if (!response.ok) throw new Error('Recovery preflight service unavailable');
  return response.json();
}
function destinationScope(env = process.env, branch = env.GITHUB_BACKUP_BRANCH) {
  if (!env.GITHUB_BACKUP_REPOSITORY || !branch || !env.GITHUB_BACKUP_PATH) {
    throw new Error('Recovery destination missing');
  }
  return crypto.createHash('sha256').update(JSON.stringify([
    env.GITHUB_BACKUP_REPOSITORY.toLowerCase(), branch, env.GITHUB_BACKUP_PATH,
  ])).digest('hex');
}
async function assertRecoveriesAcknowledged(request = requestJSON, env = process.env) {
  const actionRepo = env.GITHUB_REPOSITORY, backupRepo = env.GITHUB_BACKUP_REPOSITORY;
  if (!actionRepo || !backupRepo || !env.RESEARCH_ACTIONS_TOKEN) throw new Error('Recovery preflight configuration missing');
  const backup = await request(`https://api.github.com/repos/${backupRepo}`, env.GITHUB_BACKUP_TOKEN);
  if (backup.private !== true) throw new Error('Recovery backup must be private');
  const branch = env.GITHUB_BACKUP_BRANCH || backup.default_branch;
  const libraryPath = env.GITHUB_BACKUP_PATH;
  if (!branch || !libraryPath) throw new Error('Recovery destination missing');
  const scope = destinationScope(env, branch);
  for (let page = 1; page <= 100; page++) {
    const listing = await request(`https://api.github.com/repos/${actionRepo}/actions/artifacts?per_page=100&page=${page}`, env.RESEARCH_ACTIONS_TOKEN);
    if (!Array.isArray(listing.artifacts)) throw new Error('Invalid artifact inventory');
    for (const artifact of listing.artifacts) {
      const scoped = /^research-recovery-v2-([a-f0-9]{64})-[0-9]+-([a-f0-9]{64})$/.exec(artifact.name);
      const legacy = /^research-recovery-[0-9]+-([a-f0-9]{64})$/.exec(artifact.name);
      if (scoped && scoped[1] !== scope) continue;
      if (!scoped && !legacy) {
        if (String(artifact.name).startsWith('research-recovery-')) throw new Error('Unknown recovery artifact identity');
        continue;
      }
      // Include the current run: restarting a worker/job is not permission to
      // bypass an unresolved envelope. Legacy unscoped artifacts fail closed.
      const digest = scoped ? scoped[2] : legacy[1];
      const receiptPath = `${libraryPath}.recovery-receipts/${digest}.json`.split('/').map(encodeURIComponent).join('/');
      const metadata = await request(`https://api.github.com/repos/${backupRepo}/contents/${receiptPath}?ref=${encodeURIComponent(branch)}`, env.GITHUB_BACKUP_TOKEN, true);
      if (!metadata) throw new Error('Unacknowledged recovery artifact');
      const receipt = JSON.parse(Buffer.from(metadata.content, 'base64').toString('utf8'));
      if (receipt.schema !== 1 || receipt.delta_sha256 !== digest || receipt.library_path !== libraryPath || !receipt.library_sha) {
        throw new Error('Recovery acknowledgement identity conflict');
      }
    }
    if (listing.artifacts.length < 100) return;
  }
  throw new Error('Recovery inventory exceeds preflight bound');
}

async function main() {
  if (process.argv[2] === '--decrypt') {
    const raw = decrypt(fs.readFileSync(process.argv[3]));
    fs.writeFileSync(process.argv[4], raw, {mode: 0o600, flag: 'wx'});
    return;
  }
  if (process.argv[2] === '--upload') {
    operation = 'upload_configuration';
    const [source, digest] = process.argv.slice(3);
    if (!/^[a-f0-9]{64}$/.test(digest)) throw new Error('Invalid recovery digest');
    const branch = process.env.GITHUB_BACKUP_BRANCH || (await requestJSON(
      `https://api.github.com/repos/${process.env.GITHUB_BACKUP_REPOSITORY}`,
      process.env.GITHUB_BACKUP_TOKEN)).default_branch;
    const name = `research-recovery-v2-${destinationScope(process.env, branch)}-${process.env.GITHUB_RUN_ATTEMPT}-${digest}`;
    const {DefaultArtifactClient} = require('@actions/artifact');
    operation = 'artifact_list';
    const client = new DefaultArtifactClient();
    // A successful immutable artifact with this digest is an idempotent receipt.
    const {artifacts} = await client.listArtifacts();
    if (artifacts.some(a => a.name === name)) return;
    const directory = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP, 'research-recovery-'));
    try {
      operation = 'artifact_encrypt';
      const encrypted = path.join(directory, `${digest}.tilrec`);
      fs.writeFileSync(encrypted, encrypt(fs.readFileSync(source)), {mode: 0o600});
      operation = 'artifact_upload';
      const receipt = await client.uploadArtifact(name, [encrypted], directory,
        {retentionDays: 30, compressionLevel: 0});
      if (!receipt.id) throw new Error('Artifact acknowledgement missing');
    } finally {
      fs.rmSync(directory, {recursive: true, force: true});
    }
    return;
  }
  try { await assertRecoveriesAcknowledged(); }
  catch {
    console.error('New research blocked: an earlier recovery is unresolved or recovery inventory cannot be verified. Inspect prior research-recovery artifacts and synchronize their bundles before retrying.');
    process.exitCode = 1;
    return;
  }
  if (process.argv[2] === '--preflight') return;
  if (process.env.RESEARCH_RECOVERY_SMOKE === '1') {
    if (process.env.GITHUB_REF !== 'refs/heads/codex/cloud-backup-capacity-smoke') {
      throw new Error('Recovery smoke requires its isolated source branch');
    }
    const result = cp.spawnSync('python', ['cloud_backup_capacity_smoke.py', 'retain'], {
      stdio: 'inherit', env: {...process.env, RETAIN_CLOUD_RECOVERY: '1'},
    });
    process.exitCode = result.status === null ? 1 : result.status;
    return;
  }
  const result = cp.spawnSync('python', ['cloud_research_worker.py'], {
    stdio: 'inherit', env: {...process.env, RETAIN_CLOUD_RECOVERY: '1'},
  });
  process.exitCode = result.status === null ? 1 : result.status;
}
module.exports = {encrypt, decrypt, assertRecoveriesAcknowledged, destinationScope, safeFailure};
if (require.main === module) main().catch((error) => {
  // Never print artifact service signed URLs, keys, or private library content.
  console.error('Research recovery operation failed. Local bundle retained; inspect service/configuration.');
  console.error('RECOVERY_DIAGNOSTIC ' + JSON.stringify(safeFailure(error)));
  process.exitCode = 1;
});
