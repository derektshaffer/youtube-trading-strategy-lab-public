import { Command } from "@tauri-apps/plugin-shell";

const state = { baseUrl: "http://127.0.0.1:8765", token: "", jobId: "" };
const service = document.querySelector("#service");
const route = document.querySelector("#route");
const progress = document.querySelector("#progress");
const run = document.querySelector("#run");
const details = document.querySelector("#details");

function randomToken() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
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
  for (let attempt = 0; attempt < 60; attempt += 1) {
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

async function startSidecar() {
  state.token = randomToken();
  const command = Command.sidecar(
    "binaries/trading-intelligence-service",
    [],
    { env: { TRADING_INTELLIGENCE_LOCAL_TOKEN: state.token } }
  );
  command.stdout.on("data", line => {
    // Do not display or persist bearer tokens. The service only reports its
    // token-file location, which is useful for diagnostics.
    details.textContent += line + "\n";
  });
  command.stderr.on("data", line => { details.textContent += line + "\n"; });
  await command.spawn();
  await waitForHealth();
}

async function submit() {
  const request = {
    job_type: "system.health",
    payload: { checks: ["runtime", "sqlite"] },
    requested_target: "auto",
    idempotency_key: "tauri-spike-health"
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
startSidecar().catch(error => { service.textContent = error.message; });
setInterval(() => poll().catch(error => { service.textContent = error.message; }), 700);
