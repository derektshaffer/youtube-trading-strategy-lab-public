const { appDataDir } = window.__TAURI__.path;
const { Command } = window.__TAURI__.shell;

const state = {
  baseUrl: "",
  token: "",
  child: null,
  chartData: [],
  timeframe: "5Min",
  visibleBars: 90,
  offsetBars: 0,
  dragging: false,
  dragStartX: 0,
  dragStartOffset: 0,
  crosshair: null,
  firstChartRenderMs: null,
  uiReadySubmitted: false
};

const service = document.querySelector("#service");
const route = document.querySelector("#route");
const progress = document.querySelector("#progress");
const run = document.querySelector("#run");
const details = document.querySelector("#details");
const canvas = document.querySelector("#chart");
const quote = document.querySelector("#quote");
const timeframeLabel = document.querySelector("#timeframe-label");
const chartMs = document.querySelector("#chart-ms");
const visibleBarsLabel = document.querySelector("#visible-bars");
const dataRoute = document.querySelector("#data-route");
const sidecarState = document.querySelector("#sidecar-state");
const showVwap = document.querySelector("#show-vwap");
const showEma = document.querySelector("#show-ema");
const timeframeButtons = Array.from(document.querySelectorAll("[data-timeframe]"));

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
  for (let attempt = 0; attempt < 180; attempt += 1) {
    try {
      const health = await api("/health");
      service.textContent = `Local service: ${health.status}`;
      sidecarState.textContent = "Ready";
      run.disabled = false;
      return;
    } catch (_error) {
      await new Promise(resolve => setTimeout(resolve, 100));
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
    const text = String(line);
    if (!text.toLowerCase().includes("service token:")) {
      details.textContent += text + "\n";
    }
  });
  command.stderr.on("data", line => { details.textContent += String(line) + "\n"; });
  state.child = await command.spawn();
  await waitForHealth();
}

async function submitJob(request, onProgress = null) {
  const decision = await api("/v1/route", {
    method: "POST",
    body: JSON.stringify(request)
  });
  const submitted = await api("/v1/jobs", {
    method: "POST",
    body: JSON.stringify(request)
  });
  const jobId = String(submitted.job.id || "");
  if (!jobId) throw new Error(`${request.job_type} returned no job id`);
  for (let attempt = 0; attempt < 600; attempt += 1) {
    const current = await api(`/v1/jobs/${jobId}`);
    if (onProgress) onProgress(current, decision);
    if (current.terminal) {
      if (current.status !== "complete") {
        throw new Error(current.error?.message || `${request.job_type} ended as ${current.status}`);
      }
      return { job: current, decision };
    }
    await new Promise(resolve => setTimeout(resolve, 75));
  }
  throw new Error(`${request.job_type} did not finish before the local timeout`);
}

function timeframeCaption(value) {
  return ({ "1Min": "1m", "5Min": "5m", "15Min": "15m", "1Hour": "1h" })[value] || value;
}

function clampChartWindow() {
  const total = state.chartData.length;
  state.visibleBars = Math.max(24, Math.min(Math.max(24, total), state.visibleBars));
  const maximumOffset = Math.max(0, total - state.visibleBars);
  state.offsetBars = Math.max(0, Math.min(maximumOffset, state.offsetBars));
}

function visibleSlice() {
  clampChartWindow();
  const end = Math.max(0, state.chartData.length - state.offsetBars);
  const start = Math.max(0, end - state.visibleBars);
  return { rows: state.chartData.slice(start, end), start, end };
}

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  const width = Math.max(1, Math.round(rect.width * ratio));
  const height = Math.max(1, Math.round(rect.height * ratio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { context, width: rect.width, height: rect.height };
}

function renderChart() {
  const started = performance.now();
  const { context: ctx, width, height } = resizeCanvas();
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#08131f";
  ctx.fillRect(0, 0, width, height);

  const { rows } = visibleSlice();
  visibleBarsLabel.textContent = String(rows.length);
  if (!rows.length) {
    ctx.fillStyle = "#7890a8";
    ctx.font = "13px -apple-system";
    ctx.fillText("Waiting for chart data…", 18, 30);
    return 0;
  }

  const pad = { left: 12, right: 68, top: 15, bottom: 28 };
  const plotWidth = Math.max(40, width - pad.left - pad.right);
  const plotHeight = Math.max(40, height - pad.top - pad.bottom);
  const values = [];
  for (const row of rows) {
    values.push(row.low, row.high);
    if (showVwap.checked) values.push(row.vwap);
    if (showEma.checked) values.push(row.ema_9);
  }
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  const span = Math.max(0.01, maximum - minimum);
  minimum -= span * 0.08;
  maximum += span * 0.08;
  const xStep = plotWidth / Math.max(1, rows.length);
  const candleWidth = Math.max(1.2, Math.min(9, xStep * 0.64));
  const xAt = index => pad.left + (index + 0.5) * xStep;
  const yAt = value => pad.top + (maximum - value) / (maximum - minimum) * plotHeight;

  ctx.lineWidth = 1;
  ctx.strokeStyle = "#172a3e";
  ctx.fillStyle = "#70869c";
  ctx.font = "10px -apple-system";
  for (let line = 0; line <= 5; line += 1) {
    const y = pad.top + plotHeight * line / 5;
    const value = maximum - (maximum - minimum) * line / 5;
    ctx.beginPath();
    ctx.moveTo(pad.left, y + 0.5);
    ctx.lineTo(pad.left + plotWidth, y + 0.5);
    ctx.stroke();
    ctx.fillText(value.toFixed(2), pad.left + plotWidth + 7, y + 3);
  }

  const timeLabelCount = 5;
  for (let marker = 0; marker < timeLabelCount; marker += 1) {
    const index = Math.min(
      rows.length - 1,
      Math.round(marker * (rows.length - 1) / Math.max(1, timeLabelCount - 1))
    );
    const x = xAt(index);
    const date = new Date(rows[index].time * 1000);
    const label = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    ctx.fillStyle = "#63798f";
    ctx.fillText(label, Math.max(2, x - 18), height - 9);
  }

  const drawLine = (key, strokeStyle) => {
    ctx.beginPath();
    ctx.strokeStyle = strokeStyle;
    ctx.lineWidth = 1.35;
    rows.forEach((row, index) => {
      const x = xAt(index);
      const y = yAt(row[key]);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  if (showVwap.checked) drawLine("vwap", "#56c9f2");
  if (showEma.checked) drawLine("ema_9", "#d5a75d");

  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    const rising = row.close >= row.open;
    const color = rising ? "#4cdda4" : "#f06f7b";
    const x = xAt(index);
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, yAt(row.high));
    ctx.lineTo(x, yAt(row.low));
    ctx.stroke();
    const top = yAt(Math.max(row.open, row.close));
    const bottom = yAt(Math.min(row.open, row.close));
    const bodyHeight = Math.max(1.2, bottom - top);
    ctx.fillRect(x - candleWidth / 2, top, candleWidth, bodyHeight);
  }

  if (state.crosshair) {
    const x = Math.max(pad.left, Math.min(pad.left + plotWidth, state.crosshair.x));
    const index = Math.max(0, Math.min(rows.length - 1, Math.floor((x - pad.left) / xStep)));
    const row = rows[index];
    const candleX = xAt(index);
    const candleY = yAt(row.close);
    ctx.strokeStyle = "#718ba3";
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(candleX, pad.top);
    ctx.lineTo(candleX, pad.top + plotHeight);
    ctx.moveTo(pad.left, candleY);
    ctx.lineTo(pad.left + plotWidth, candleY);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#d9e8f5";
    ctx.font = "11px -apple-system";
    ctx.fillText(
      `O ${row.open.toFixed(2)}  H ${row.high.toFixed(2)}  L ${row.low.toFixed(2)}  C ${row.close.toFixed(2)}`,
      pad.left + 8,
      pad.top + 15
    );
  }

  const latest = rows[rows.length - 1];
  quote.textContent = `$${latest.close.toFixed(2)} · synthetic`;
  const elapsed = performance.now() - started;
  if (state.firstChartRenderMs === null) state.firstChartRenderMs = elapsed;
  chartMs.textContent = `${elapsed.toFixed(2)} ms`;
  return elapsed;
}

async function registerUiReady() {
  if (state.uiReadySubmitted || state.firstChartRenderMs === null) return;
  state.uiReadySubmitted = true;
  const request = {
    job_type: "system.health",
    payload: {
      checks: ["tauri-ui", "chart-rendered", "authenticated-sidecar"],
      client_metrics: {
        framework: "tauri",
        chart_render_ms: Number(state.firstChartRenderMs.toFixed(4)),
        chart_bars: state.chartData.length,
        timeframe: state.timeframe,
        ui_ready_monotonic_ms: Number(performance.now().toFixed(4))
      }
    },
    requested_target: "auto",
    idempotency_key: `tauri-ui-ready-${Date.now()}`
  };
  await submitJob(request);
}

async function loadChart(timeframe) {
  state.timeframe = timeframe;
  timeframeLabel.textContent = timeframeCaption(timeframe);
  timeframeButtons.forEach(button => {
    button.classList.toggle("active", button.dataset.timeframe === timeframe);
    button.disabled = true;
  });
  const request = {
    job_type: "chart.framework_fixture",
    payload: { symbol: "SDOT", timeframe, bars: 220 },
    requested_target: "auto",
    idempotency_key: `tauri-chart-${timeframe}-${Date.now()}`
  };
  try {
    const { job, decision } = await submitJob(request, (current, currentDecision) => {
      progress.value = Number(current.progress || 0);
      service.textContent = `${current.status} · ${current.stage}`;
      route.textContent = `Route: ${currentDecision.target} — ${currentDecision.reason}`;
      dataRoute.textContent = currentDecision.target;
    });
    state.chartData = Array.isArray(job.result?.candles) ? job.result.candles : [];
    state.visibleBars = Math.min(90, Math.max(24, state.chartData.length));
    state.offsetBars = 0;
    state.crosshair = null;
    renderChart();
    service.textContent = `Chart ready · ${state.chartData.length} bars`;
    progress.value = 1;
    await registerUiReady();
  } finally {
    timeframeButtons.forEach(button => { button.disabled = false; });
  }
}

async function submitHealth() {
  run.disabled = true;
  const request = {
    job_type: "system.health",
    payload: { checks: ["runtime", "sqlite", "manual-button"] },
    requested_target: "auto",
    idempotency_key: `tauri-spike-health-${Date.now()}`
  };
  try {
    const { job, decision } = await submitJob(request, (current, currentDecision) => {
      progress.value = Number(current.progress || 0);
      service.textContent = `${current.status} · ${current.stage}`;
      route.textContent = `Route: ${currentDecision.target} — ${currentDecision.reason}`;
    });
    details.textContent = JSON.stringify(job.result || {}, null, 2);
    service.textContent = "Local health job complete";
    route.textContent = `Route: ${decision.target} — ${decision.reason}`;
  } finally {
    run.disabled = false;
  }
}

function localPointer(event) {
  const rect = canvas.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

canvas.addEventListener("wheel", event => {
  event.preventDefault();
  const direction = event.deltaY > 0 ? 1 : -1;
  state.visibleBars = Math.round(state.visibleBars * (direction > 0 ? 1.12 : 0.89));
  clampChartWindow();
  renderChart();
}, { passive: false });

canvas.addEventListener("pointerdown", event => {
  const point = localPointer(event);
  state.dragging = true;
  state.dragStartX = point.x;
  state.dragStartOffset = state.offsetBars;
  canvas.setPointerCapture(event.pointerId);
});

canvas.addEventListener("pointermove", event => {
  const point = localPointer(event);
  state.crosshair = point;
  if (state.dragging) {
    const plotWidth = Math.max(40, canvas.getBoundingClientRect().width - 80);
    const pixelsPerBar = plotWidth / Math.max(1, state.visibleBars);
    const deltaBars = Math.round((point.x - state.dragStartX) / Math.max(1, pixelsPerBar));
    state.offsetBars = state.dragStartOffset + deltaBars;
    clampChartWindow();
  }
  renderChart();
});

canvas.addEventListener("pointerup", event => {
  state.dragging = false;
  try { canvas.releasePointerCapture(event.pointerId); } catch (_error) {}
});

canvas.addEventListener("pointercancel", () => { state.dragging = false; });
canvas.addEventListener("pointerleave", () => {
  if (!state.dragging) {
    state.crosshair = null;
    renderChart();
  }
});

timeframeButtons.forEach(button => {
  button.addEventListener("click", () => {
    loadChart(button.dataset.timeframe).catch(error => {
      service.textContent = error.message;
      details.textContent = error.stack || error.message;
    });
  });
});

document.querySelector("#reset-chart").addEventListener("click", () => {
  state.visibleBars = Math.min(90, Math.max(24, state.chartData.length));
  state.offsetBars = 0;
  state.crosshair = null;
  renderChart();
});
showVwap.addEventListener("change", renderChart);
showEma.addEventListener("change", renderChart);
run.addEventListener("click", () => {
  submitHealth().catch(error => {
    service.textContent = error.message;
    details.textContent = error.stack || error.message;
  });
});
window.addEventListener("beforeunload", () => { void stopSidecar(); });
new ResizeObserver(() => renderChart()).observe(canvas);

run.disabled = true;
startSidecar()
  .then(() => loadChart("5Min"))
  .catch(error => {
    service.textContent = error.message;
    details.textContent = error.stack || error.message;
  });
