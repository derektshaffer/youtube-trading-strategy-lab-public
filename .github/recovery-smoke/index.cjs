const smoke = require('../recovery-artifact/smoke.cjs');
smoke.main().catch(() => {
  console.error('Controlled recovery smoke failed; payloads, credentials and signed URLs withheld.');
  console.error('SMOKE_DIAGNOSTIC ' + JSON.stringify({stage: smoke.failureStage()}));
  process.exitCode = 1;
});
