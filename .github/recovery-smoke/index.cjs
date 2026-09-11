require('../recovery-artifact/smoke.cjs').main().catch(() => {
  console.error('Controlled recovery smoke failed; payloads, credentials and signed URLs withheld.');
  process.exitCode = 1;
});
