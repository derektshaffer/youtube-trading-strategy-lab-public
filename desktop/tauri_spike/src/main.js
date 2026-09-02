const { appDataDir } = window.__TAURI__.path;
const { Command } = window.__TAURI__.shell;

const state = { baseUrl: "", token: "", jobId: "", child: null };
const service = document.querySelector("#service");
const route = document.querySelector("#route");
const progress = document.querySelector("#progress");
const run = document.querySelector("#run");
const details = document.querySelector("#details");

function randomToken() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
}

function randomPort() {
  const value = crypto.getRandomValues(new Uint32Array(1))[0];
  return 20000 + (value % 30000);
}

async function api(path, options = {}) {
  const response = await fetch(state.baseUrl + path, {
    ...options,
    headers: {
      "Authorization": `Bearer ${state.token}`,
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function waitForHealth() {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const health = await api("/health");
      service.textContent = `Local service: ${health.status}`;
      run.disabled = false;
      return;
    } catch (_error) {
      await new Promise(resolve => setTimeout(resolve, 150));
    }
  }
  throw new Error("The local Python service did not become ready.");
}

async function stopSidecar() {
  const child = state.child;
  state.child = null;
  if (child) {
    try {
      await child.kill();
    } catch (_error) {
      // The child may already have stopped with the parent application.
    }
  }
}

async function startSidecar() {
  state.token = randomToken();
  const port = randomPort();
  state.baseUrl = `http://127.0.0.1:${port}`;
  const dataDir = await appDataDir();
  const command = Command.sidecar(
    "binaries/trading-intelligence-service",
    ["--host", "127.0.0.1", "--port", String(port)],
    {
      env: {
        TRADING_INTELLIGENCE_LOCAL_TOKEN: state.token,
        TRADING_INTELLIGENCE_DESKTOP_DATA_DIR: dataDir
      }
    }
  );
  command.stdout.on("data", line => {
    // Never display or persist bearer tokens. The sidecar reports only its
    // owner-readable token-file location and loopback URL.
    details.textContent += line + "\n";
  });
  command.stderr.on("data", line => { details.textContent += line + "\n"; });
  state.child = await command.spawn();
  await waitForHealth();
}

async function submit() {
  const request = {
    job_type: "system.health",
    payload: { checks: ["runtime", "sqlite"] },
    requested_target: "auto",
    idempotency_key: `tauri-spike-health-${Date.now()}`
  };
  const decision = await api("/v1/route", { method: "POST", body: JSON.stringify(request) });
  route.textContent = `Route: ${decision.target} — ${decision.reason}`;
  const submitted = await api("/v1/jobs", { method: "POST", body: JSON.stringify(request) });
  state.jobId = submitted.job.id;
  run.disabled = true;
}

async function poll() {
  if (!state.jobId || !state.token) return;
  const job = await api(`/v1/jobs/${state.jobId}`);
  progress.value = job.progress || 0;
  service.textContent = `${job.status} · ${job.stage}`;
  if (job.terminal) {
    details.textContent = JSON.stringify(job.result || job.error || {}, null, 2);
    state.jobId = "";
    run.disabled = false;
  }
}

run.disabled = true;
run.addEventListener("click", () => submit().catch(error => { service.textContent = error.message; }));
window.addEventListener("beforeunload", () => { void stopSidecar(); });
startSidecar().catch(error => { service.textContent = error.message; });
setInterval(() => poll().catch(error => { service.textContent = error.message; }), 700);
