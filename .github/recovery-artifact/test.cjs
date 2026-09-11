const {test} = require('node:test');
const assert = require('node:assert/strict');
const {encrypt, decrypt} = require('./index.cjs');
process.env.GITHUB_BACKUP_TOKEN = 'fixture-token-not-a-secret';
test('encrypted bundle round-trips without plaintext exposure', () => {
  const raw = Buffer.from('private-completed-fixture-result');
  const sealed = encrypt(raw);
  assert.deepEqual(decrypt(sealed), raw);
  assert.equal(sealed.includes(raw), false);
  assert.notDeepEqual(encrypt(raw), sealed);
});
test('tampering is rejected', () => {
  const sealed = encrypt(Buffer.from('fixture'));
  sealed[sealed.length - 1] ^= 1;
  assert.throws(() => decrypt(sealed));
});
test('wrong encryption key is rejected', () => {
  const sealed = encrypt(Buffer.from('fixture'));
  process.env.GITHUB_BACKUP_TOKEN = 'wrong-key';
  try { assert.throws(() => decrypt(sealed)); }
  finally { process.env.GITHUB_BACKUP_TOKEN = 'fixture-token-not-a-secret'; }
});
const {assertRecoveriesAcknowledged} = require('./index.cjs');
const env = {GITHUB_REPOSITORY: 'fixture/action', GITHUB_BACKUP_REPOSITORY: 'fixture/private',
  RESEARCH_ACTIONS_TOKEN: 'fake-actions-token', GITHUB_BACKUP_TOKEN: 'fake-backup-token',
  GITHUB_BACKUP_PATH: 'library.json', GITHUB_RUN_ID: '200'};
const digest = 'a'.repeat(64);
const artifact = {name: `research-recovery-1-${digest}`, workflow_run: {id: 100}};
function fakeRequest(receipt, artifacts = [artifact]) {
  return async (url) => {
    if (url.includes('/actions/artifacts')) return {artifacts};
    if (url.includes('/contents/')) return receipt ? {content: Buffer.from(JSON.stringify(receipt)).toString('base64')} : null;
    return {private: true, default_branch: 'main'};
  };
}
test('unacknowledged prior recovery blocks future computation', async () => {
  await assert.rejects(assertRecoveriesAcknowledged(fakeRequest(null), env));
});
test('matching private receipt permits future computation', async () => {
  await assertRecoveriesAcknowledged(fakeRequest({schema: 1, delta_sha256: digest,
    library_path: 'library.json', library_sha: 'fixture-sha'}), env);
});
test('foreign acknowledgement fails closed', async () => {
  await assert.rejects(assertRecoveriesAcknowledged(fakeRequest({schema: 1, delta_sha256: 'wrong',
    library_path: 'library.json', library_sha: 'fixture-sha'}), env));
});
test('artifact inventory failure blocks future computation', async () => {
  await assert.rejects(assertRecoveriesAcknowledged(async () => {throw new Error('unavailable')}, env));
});
