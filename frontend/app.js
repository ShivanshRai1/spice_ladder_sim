const API_BASE = "http://127.0.0.1:5000";
const SIM_API_URL = `${API_BASE}/api/simulate`;
const SCHEMATIC_API_URL = `${API_BASE}/api/schematic`;

const DEFAULT_R = 0.1;
const DEFAULT_C = 1.0;
const PLOT_TARGET_POINTS = 1500;
const PROFILE_TABLE_MAX_ROWS = 1200;
const METHOD_DESCRIPTIONS = {
  exact_zoh: "exact_zoh: exact branch-state update with zero-order held power in each step.",
  exact_trap: "exact_trap: exact branch-state update with averaged endpoint power per step.",
  backward_euler: "backward_euler: implicit A-stable method, robust for stiff thermal ladders.",
  trapezoidal: "trapezoidal: bilinear/Crank-Nicolson method, A-stable and more accurate for smooth transients.",
};

const state = {
  model: "foster",
  N: 4,
  method: "exact_zoh",
  ambient: 0,
  overlayPower: false,
  profileDt: 0.001,
  simTotalTime: 2.0,
  Rth: [0.2, 0.3, 0.1, 0.4],
  Cth: [5, 10, 2, 20],
  branchLabels: ["", "", "", ""],
  profile: { t: [], p: [] },
  profileSource: "pulse",
  profileOnDuration: NaN,
  result: null,
  lastRunProfile: null,
};

const el = {};
let chart = null;
let schematicRequestSeq = 0;

function byId(id) {
  return document.getElementById(id);
}

function initElements() {
  el.modelSelect = byId("modelSelect");
  el.orderInput = byId("orderInput");
  el.methodSelect = byId("methodSelect");
  el.ambientInput = byId("ambientInput");
  el.overlayPowerToggle = byId("overlayPowerToggle");
  el.exportCsvBtn = byId("exportCsvBtn");

  el.rthPaste = byId("rthPaste");
  el.cthPaste = byId("cthPaste");
  el.applyPasteBtn = byId("applyPasteBtn");
  el.labelColHeader = byId("labelColHeader");
  el.methodHelpAll = byId("methodHelpAll");

  el.paramBody = byId("paramTable").querySelector("tbody");

  el.profileBody = byId("profileTable").querySelector("tbody");
  el.profileTableHint = byId("profileTableHint");
  el.profileUploadHint = byId("profileUploadHint");
  el.addProfileRowBtn = byId("addProfileRowBtn");
  el.sortProfileBtn = byId("sortProfileBtn");
  el.uploadProfileCsvBtn = byId("uploadProfileCsvBtn");
  el.uploadProfileCsvInput = byId("uploadProfileCsvInput");

  el.profileDt = byId("profileDt");
  el.simTimeInput = byId("simTimeInput");

  el.pulsePeriod = byId("pulsePeriod");
  el.pulsePower = byId("pulsePower");
  el.pulseOnDuration = byId("pulseOnDuration");
  el.genPulseBtn = byId("genPulseBtn");

  el.stepPower = byId("stepPower");
  el.genStepBtn = byId("genStepBtn");

  el.runBtn = byId("runBtn");
  el.runSpinner = byId("runSpinner");
  el.dtTauHint = byId("dtTauHint");
  el.validationHint = byId("validationHint");
  el.errorPanel = byId("errorPanel");

  el.schematic = byId("schematic");
  el.chartCanvas = byId("resultChart");

  el.dcRthStat = byId("dcRthStat");
  el.avgPowerStat = byId("avgPowerStat");
  el.steadyStateTempStat = byId("steadyStateTempStat");
  el.pointsGeneratedStat = byId("pointsGeneratedStat");
  el.maxTempStat = byId("maxTempStat");
  el.maxRiseStat = byId("maxRiseStat");
}

function setError(message) {
  el.errorPanel.textContent = message || "";
}

function setValidation(message, ok) {
  el.validationHint.textContent = message;
  el.validationHint.style.color = ok ? "#28675f" : "#9f2d2d";
}

function parseNumberList(text) {
  return text
    .split(/[\s,]+/)
    .map((v) => v.trim())
    .filter((v) => v.length > 0)
    .map((v) => Number(v));
}

function parsePastedGrid(text) {
  const rows = text
    .replace(/\r/g, "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  if (!rows.length) return [];

  const grid = [];
  for (const row of rows) {
    const parts = row.includes("\t")
      ? row.split("\t")
      : row.split(/[\s,]+/);

    const nums = parts
      .map((v) => v.trim())
      .filter((v) => v.length > 0)
      .map((v) => Number(v));

    if (!nums.length || nums.some((v) => !Number.isFinite(v))) {
      return null;
    }
    grid.push(nums);
  }

  return grid;
}

function setProfileUploadHint(message) {
  if (!el.profileUploadHint) return;
  el.profileUploadHint.textContent = message || "";
}

function parseCsvLine(line) {
  const values = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === "\"") {
      if (inQuotes && line[i + 1] === "\"") {
        current += "\"";
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (ch === "," && !inQuotes) {
      values.push(current.trim());
      current = "";
      continue;
    }
    current += ch;
  }
  values.push(current.trim());
  return values;
}

function normalizeHeaderName(name) {
  return String(name || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

function inferOnDurationFromProfile(tVals, pVals) {
  if (!Array.isArray(tVals) || !Array.isArray(pVals) || tVals.length < 2 || pVals.length !== tVals.length) {
    return NaN;
  }

  let minOnDuration = Number.POSITIVE_INFINITY;
  let currentSegment = 0;
  let active = false;

  for (let i = 0; i < tVals.length - 1; i += 1) {
    const dt = tVals[i + 1] - tVals[i];
    if (!Number.isFinite(dt) || dt <= 0) continue;

    if (pVals[i] > 0) {
      currentSegment += dt;
      active = true;
    } else if (active) {
      minOnDuration = Math.min(minOnDuration, currentSegment);
      currentSegment = 0;
      active = false;
    }
  }

  if (active) {
    minOnDuration = Math.min(minOnDuration, currentSegment);
  }

  return Number.isFinite(minOnDuration) ? minOnDuration : NaN;
}

function parseProfileCsv(text) {
  const lines = String(text || "")
    .replace(/\r/g, "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  if (!lines.length) {
    throw new Error("CSV file is empty.");
  }

  const rows = lines.map(parseCsvLine);
  const firstRow = rows[0];
  const firstTwoNumeric = firstRow.length >= 2
    && Number.isFinite(Number(firstRow[0]))
    && Number.isFinite(Number(firstRow[1]));

  let startIdx = 0;
  let tIdx = 0;
  let pIdx = 1;

  if (!firstTwoNumeric) {
    const headerNorm = firstRow.map(normalizeHeaderName);
    const tCandidate = headerNorm.findIndex((v) => v === "t" || v === "time" || v === "times" || v === "ts");
    const pCandidate = headerNorm.findIndex((v) => v === "p" || v === "power" || v === "pw" || v === "powerw");
    if (tCandidate < 0 || pCandidate < 0) {
      throw new Error("CSV with header must include columns named t and P.");
    }
    tIdx = tCandidate;
    pIdx = pCandidate;
    startIdx = 1;
  }

  const t = [];
  const p = [];
  for (let i = startIdx; i < rows.length; i += 1) {
    const row = rows[i];
    if (Math.max(tIdx, pIdx) >= row.length) {
      throw new Error(`CSV row ${i + 1} does not contain required t/P columns.`);
    }

    const tVal = Number(row[tIdx]);
    const pVal = Number(row[pIdx]);
    if (!Number.isFinite(tVal) || !Number.isFinite(pVal)) {
      throw new Error(`CSV row ${i + 1} has non-numeric t or P value.`);
    }
    t.push(tVal);
    p.push(pVal);
  }

  if (t.length < 2) {
    throw new Error("CSV must contain at least two rows of numeric t, P points.");
  }
  for (let i = 1; i < t.length; i += 1) {
    if (!(t[i] > t[i - 1])) {
      throw new Error("CSV time values must be strictly increasing.");
    }
  }

  return { t, p };
}

function refreshProfileOnDurationMeta() {
  if (state.profileSource === "pulse" || state.profileSource === "csv") {
    state.profileOnDuration = inferOnDurationFromProfile(state.profile.t, state.profile.p);
  } else {
    state.profileOnDuration = NaN;
  }
}

function resizeParamArrays(newN) {
  const oldN = state.N;
  if (newN > oldN) {
    for (let i = oldN; i < newN; i += 1) {
      state.Rth.push(DEFAULT_R);
      state.Cth.push(DEFAULT_C);
      state.branchLabels.push("");
    }
  } else if (newN < oldN) {
    state.Rth = state.Rth.slice(0, newN);
    state.Cth = state.Cth.slice(0, newN);
    state.branchLabels = state.branchLabels.slice(0, newN);
  }
  state.N = newN;
}

function buildTimeGrid(dt, totalTime) {
  const n = Math.floor(totalTime / dt + 1e-12);
  const t = new Array(n + 1);
  for (let i = 0; i <= n; i += 1) {
    t[i] = Number((i * dt).toFixed(12));
  }
  const last = t[t.length - 1];
  if (last < totalTime - 1e-12) {
    t.push(Number(totalTime.toFixed(12)));
  }
  return t;
}

function generatePulseProfile(period, peakPower, onDuration, dt, totalTime) {
  const t = buildTimeGrid(dt, totalTime);
  const p = t.map((time) => {
    if (time >= totalTime - 1e-12) return 0;
    const phase = time % period;
    return phase < onDuration ? peakPower : 0;
  });
  return { t, p };
}

function generateStepProfile(power, dt, totalTime) {
  const t = buildTimeGrid(dt, totalTime);
  const p = t.map(() => power);
  return { t, p };
}

function ensureNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : NaN;
}

function validateState() {
  if (!Number.isInteger(state.N) || state.N < 1) {
    return { ok: false, msg: "N must be an integer >= 1." };
  }
  if (state.Rth.length !== state.N || state.Cth.length !== state.N) {
    return { ok: false, msg: "Rth/Cth lengths must match N." };
  }
  for (let i = 0; i < state.N; i += 1) {
    const r = state.Rth[i];
    const c = state.Cth[i];
    if (!Number.isFinite(r) || r <= 0) {
      return { ok: false, msg: `Rth[${i}] must be positive.` };
    }
    if (!Number.isFinite(c) || c <= 0) {
      return { ok: false, msg: `Cth[${i}] must be positive.` };
    }
  }
  if (!Number.isFinite(state.ambient)) {
    return { ok: false, msg: "Ambient must be numeric." };
  }
  if (!Number.isFinite(state.profileDt) || state.profileDt <= 0) {
    return { ok: false, msg: "dt must be > 0." };
  }
  if (!Number.isFinite(state.simTotalTime) || state.simTotalTime <= 0) {
    return { ok: false, msg: "Total simulation time must be > 0." };
  }

  const { t, p } = state.profile;
  if (!Array.isArray(t) || !Array.isArray(p) || t.length !== p.length || t.length < 2) {
    return { ok: false, msg: "Profile arrays t and p must have equal length >= 2." };
  }
  for (let i = 0; i < t.length; i += 1) {
    if (!Number.isFinite(t[i]) || !Number.isFinite(p[i])) {
      return { ok: false, msg: `Profile row ${i} has invalid numeric values.` };
    }
    if (i > 0 && !(t[i] > t[i - 1])) {
      return { ok: false, msg: "Time values must be strictly increasing." };
    }
  }
  return { ok: true, msg: "Inputs valid. Ready to simulate." };
}

function updateRunState() {
  const check = validateState();
  el.runBtn.disabled = !check.ok;
  setValidation(check.msg, check.ok);
  updateDtTauHint();
  updateStats();
}

function getMinTau() {
  if (!state.Rth.length || state.Rth.length !== state.Cth.length) {
    return NaN;
  }
  let minTau = Number.POSITIVE_INFINITY;
  for (let i = 0; i < state.Rth.length; i += 1) {
    const r = state.Rth[i];
    const c = state.Cth[i];
    if (!Number.isFinite(r) || !Number.isFinite(c) || r <= 0 || c <= 0) {
      continue;
    }
    minTau = Math.min(minTau, r * c);
  }
  return Number.isFinite(minTau) ? minTau : NaN;
}

function updateDtTauHint() {
  if (!el.dtTauHint || !el.profileDt) return;

  const minTau = getMinTau();
  let message = Number.isFinite(minTau)
    ? `The time step should be <= minimum tau (${minTau.toExponential(3)} s).`
    : "The time step should be less than or equal to minimum tau.";

  if (state.profileSource === "pulse" || state.profileSource === "csv") {
    const onDurationText = Number.isFinite(state.profileOnDuration)
      ? `${state.profileOnDuration.toExponential(3)} s`
      : "N/A";
    if (Number.isFinite(minTau)) {
      message = `The time step should be <= minimum tau (${minTau.toExponential(3)} s) and On-duration (${onDurationText}).`;
    } else {
      message = `The time step should be less than or equal to minimum tau and On-duration (${onDurationText}).`;
    }
  }

  el.dtTauHint.textContent = message;
  el.profileDt.title = message;
}

function updateStats() {
  const dc = state.Rth.reduce((acc, v) => (Number.isFinite(v) ? acc + v : acc), 0);
  el.dcRthStat.textContent = dc.toFixed(2);

  const pointsGenerated = Array.isArray(state.profile.t) ? state.profile.t.length : NaN;
  el.pointsGeneratedStat.textContent = Number.isFinite(pointsGenerated)
    ? pointsGenerated.toFixed(0)
    : "-";

  const ambient = Number.isFinite(state.ambient) ? state.ambient : 0;
  let avgPower = NaN;
  const tSrc = state.profile.t;
  const pSrc = state.profile.p;
  if (Array.isArray(tSrc) && Array.isArray(pSrc) && tSrc.length > 1 && pSrc.length === tSrc.length) {
    const duration = tSrc[tSrc.length - 1] - tSrc[0];
    if (duration > 0) {
      let energy = 0;
      for (let k = 0; k < tSrc.length - 1; k += 1) {
        energy += pSrc[k] * (tSrc[k + 1] - tSrc[k]);
      }
      avgPower = energy / duration;
    }
  }
  el.avgPowerStat.textContent = Number.isFinite(avgPower) ? avgPower.toFixed(2) : "-";
  const steadyStateTemp = Number.isFinite(avgPower) ? (avgPower * dc + ambient) : NaN;
  el.steadyStateTempStat.textContent = Number.isFinite(steadyStateTemp)
    ? steadyStateTemp.toFixed(2)
    : "-";

  if (!state.result) {
    if (el.exportCsvBtn) el.exportCsvBtn.disabled = true;
    el.maxTempStat.textContent = "-";
    el.maxRiseStat.textContent = "-";
    return;
  }
  if (el.exportCsvBtn) el.exportCsvBtn.disabled = false;

  let riseSeries = [];
  if (state.result.model === "foster") {
    riseSeries = state.result.Tj || [];
  } else {
    riseSeries = state.result.T_nodes?.[0] || [];
  }

  if (!riseSeries.length) {
    el.maxTempStat.textContent = "-";
    el.maxRiseStat.textContent = "-";
    return;
  }

  const maxRise = Math.max(...riseSeries);
  const maxTemp = maxRise + ambient;
  el.maxTempStat.textContent = Number.isFinite(maxTemp) ? maxTemp.toFixed(2) : "-";
  el.maxRiseStat.textContent = Number.isFinite(maxRise) ? maxRise.toFixed(2) : "-";
}

function escapeCsv(value) {
  const s = String(value ?? "");
  if (s.includes(",") || s.includes("\"") || s.includes("\n")) {
    return `"${s.replace(/"/g, "\"\"")}"`;
  }
  return s;
}

function exportCsv() {
  if (!state.result || !state.lastRunProfile) {
    setError("Run a simulation before exporting CSV.");
    return;
  }

  const t = state.result.t || [];
  const p = state.lastRunProfile.p || [];
  if (!Array.isArray(t) || !Array.isArray(p) || t.length !== p.length) {
    setError("CSV export failed: time/power lengths do not match.");
    return;
  }

  const lines = [];
  const ambient = Number.isFinite(state.ambient) ? state.ambient : 0;
  if (state.result.model === "foster") {
    const tj = state.result.Tj || [];
    if (!Array.isArray(tj) || tj.length !== t.length) {
      setError("CSV export failed: foster result length mismatch.");
      return;
    }
    lines.push(["time_s", "power_W", "temp_node0_C", "temp_rise_node0_C"].join(","));
    for (let i = 0; i < t.length; i += 1) {
      lines.push([t[i], p[i], tj[i] + ambient, tj[i]].map(escapeCsv).join(","));
    }
  } else {
    const nodes = state.result.T_nodes || [];
    if (!Array.isArray(nodes) || !nodes.length) {
      setError("CSV export failed: missing cauer node data.");
      return;
    }
    if (nodes.some((arr) => !Array.isArray(arr) || arr.length !== t.length)) {
      setError("CSV export failed: cauer node lengths do not match time.");
      return;
    }

    const headers = ["time_s", "power_W"];
    for (let i = 0; i < nodes.length; i += 1) {
      const custom = (state.branchLabels[i] || "").trim();
      headers.push(custom ? `temp_branch_${i}_${custom}_C` : `temp_branch_${i}_C`);
      headers.push(custom ? `temp_rise_branch_${i}_${custom}_C` : `temp_rise_branch_${i}_C`);
    }
    lines.push(headers.map(escapeCsv).join(","));

    for (let row = 0; row < t.length; row += 1) {
      const vals = [t[row], p[row]];
      for (let i = 0; i < nodes.length; i += 1) {
        vals.push(nodes[i][row] + ambient);
        vals.push(nodes[i][row]);
      }
      lines.push(vals.map(escapeCsv).join(","));
    }
  }

  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `thermal_${state.result.model}_${Date.now()}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function renderParamTable() {
  const showLabelCol = state.model === "cauer";
  if (el.labelColHeader) {
    el.labelColHeader.style.display = showLabelCol ? "" : "none";
  }

  const rows = [];
  for (let i = 0; i < state.N; i += 1) {
    const r = state.Rth[i];
    const c = state.Cth[i];
    const tau = Number.isFinite(r) && Number.isFinite(c) ? r * c : NaN;

    rows.push(`
      <tr>
        <td>${i + 1}</td>
        <td><input class="param-input" data-kind="Rth" data-index="${i}" type="number" step="any" value="${r}" /></td>
        <td><input class="param-input" data-kind="Cth" data-index="${i}" type="number" step="any" value="${c}" /></td>
        <td class="readonly">${Number.isFinite(tau) ? tau.toFixed(6) : "-"}</td>
        ${showLabelCol
          ? `<td><input class="param-label-input" data-index="${i}" type="text" value="${state.branchLabels[i] || ""}" placeholder="optional" /></td>`
          : ""}
      </tr>
    `);
  }
  el.paramBody.innerHTML = rows.join("");
}

function updateMethodHelp() {
  if (!el.methodHelpAll) return;
  const current = METHOD_DESCRIPTIONS[state.method] || "";
  el.methodHelpAll.textContent = current;
}

function getDecimatedIndices(length, targetCount) {
  if (length <= targetCount) {
    return { indices: Array.from({ length }, (_, i) => i), stride: 1 };
  }
  const stride = Math.ceil(length / targetCount);
  const indices = [];
  for (let i = 0; i < length; i += stride) indices.push(i);
  if (indices[indices.length - 1] !== length - 1) indices.push(length - 1);
  return { indices, stride };
}

function renderProfileTable() {
  const totalRows = state.profile.t.length;
  const { indices, stride } = getDecimatedIndices(totalRows, PROFILE_TABLE_MAX_ROWS);

  const rows = [];
  for (const idx of indices) {
    rows.push(`
      <tr>
        <td>${idx}</td>
        <td><input class="profile-input" data-kind="t" data-index="${idx}" type="number" step="any" value="${state.profile.t[idx]}" /></td>
        <td><input class="profile-input" data-kind="p" data-index="${idx}" type="number" step="any" value="${state.profile.p[idx]}" /></td>
        <td><button class="remove-row-btn" data-index="${idx}" type="button">x</button></td>
      </tr>
    `);
  }
  el.profileBody.innerHTML = rows.join("");

  if (stride > 1) {
    el.profileTableHint.textContent =
      `Showing ${indices.length} of ${totalRows} rows (every ${stride}th row) to keep UI responsive.`;
  } else {
    el.profileTableHint.textContent = "";
  }
}

async function renderSchematic() {
  const requestId = ++schematicRequestSeq;
  const payload = {
    model: state.model,
    N: state.N,
    Rth: state.Rth,
    Cth: state.Cth,
    theme: "light",
  };

  el.schematic.innerHTML = "<div class='hint'>Loading schematic...</div>";

  try {
    const res = await fetch(SCHEMATIC_API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.error || `Schematic request failed (${res.status})`);
    }

    if (requestId !== schematicRequestSeq) return;

    const svg = body.svg;
    if (typeof svg !== "string" || !svg.toLowerCase().includes("<svg")) {
      throw new Error("Schematic response did not include valid SVG.");
    }

    // Reuse builder-provided SVG verbatim.
    el.schematic.innerHTML = svg;
  } catch (err) {
    if (requestId !== schematicRequestSeq) return;
    el.schematic.innerHTML = "<div class='hint'>Schematic unavailable.</div>";
    setError(err.message || String(err));
  }
}

function colorForIndex(i, count) {
  const hue = Math.round((360 * i) / Math.max(1, count));
  return `hsl(${hue}, 68%, 42%)`;
}

function preparePlotData(t, seriesList, powerSeries) {
  const { indices, stride } = getDecimatedIndices(t.length, PLOT_TARGET_POINTS);
  const tOut = indices.map((i) => t[i]);
  const yOut = seriesList.map((series) => indices.map((i) => series[i]));
  const pOut = powerSeries ? indices.map((i) => powerSeries[i]) : null;
  return { t: tOut, y: yOut, p: pOut, stride };
}

function renderPlots(result) {
  if (!result) return;

  const t = result.t;
  const riseSeriesList = result.model === "foster" ? [result.Tj] : result.T_nodes;
  const ambient = Number.isFinite(state.ambient) ? state.ambient : 0;
  const tempSeriesList = riseSeriesList.map((series) => series.map((v) => v + ambient));
  const labels = result.model === "foster"
    ? ["node 0 / junction"]
    : tempSeriesList.map((_, i) => {
      const custom = (state.branchLabels[i] || "").trim();
      return custom ? `branch ${i} (${custom})` : `branch ${i}`;
    });

  const sampled = preparePlotData(t, tempSeriesList, state.profile.p);

  const datasets = sampled.y.map((series, i) => ({
    label: labels[i],
    data: sampled.t.map((time, j) => ({ x: time, y: series[j] })),
    borderColor: colorForIndex(i, sampled.y.length),
    backgroundColor: "transparent",
    pointRadius: 0,
    borderWidth: 1.6,
    tension: 0,
    yAxisID: "yTemp",
  }));

  if (state.overlayPower && sampled.p) {
    datasets.push({
      label: "power",
      data: sampled.t.map((time, j) => ({ x: time, y: sampled.p[j] })),
      borderColor: "#8b3a3a",
      backgroundColor: "transparent",
      pointRadius: 0,
      borderWidth: 1.2,
      borderDash: [6, 4],
      tension: 0,
      yAxisID: "yPower",
    });
  }

  if (chart) {
    chart.destroy();
  }

  chart = new Chart(el.chartCanvas, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      parsing: false,
      normalized: true,
      scales: {
        x: {
          type: "linear",
          title: { display: true, text: "time (s)" },
        },
        yTemp: {
          type: "linear",
          position: "left",
          title: { display: true, text: "temperature (°C)" },
        },
        yPower: {
          type: "linear",
          position: "right",
          title: { display: true, text: "power (W)" },
          display: state.overlayPower,
          grid: { drawOnChartArea: false },
        },
      },
      plugins: {
        legend: { display: true },
        tooltip: { mode: "index", intersect: false },
      },
      interaction: { mode: "nearest", axis: "x", intersect: false },
    },
  });

  if (sampled.stride > 1) {
    setValidation(
      `Inputs valid. Plot decimation active: every ${sampled.stride}th point shown for UI responsiveness.`,
      true
    );
  }
}

function refreshMethodOptions() {
  const opts = state.model === "foster"
    ? ["exact_zoh", "exact_trap"]
    : ["backward_euler", "trapezoidal"];

  if (!opts.includes(state.method)) {
    state.method = opts[0];
  }

  el.methodSelect.innerHTML = opts
    .map((m) => `<option value="${m}">${m}</option>`)
    .join("");
  el.methodSelect.value = state.method;
  updateMethodHelp();
}

async function runSimulation() {
  const check = validateState();
  if (!check.ok) {
    updateRunState();
    return;
  }

  setError("");
  el.runBtn.disabled = true;
  el.runSpinner.classList.remove("hidden");

  const payload = {
    model: state.model,
    N: state.N,
    Rth: state.Rth,
    Cth: state.Cth,
    t: state.profile.t,
    p: state.profile.p,
    method: state.method,
    ambient: state.ambient,
  };

  try {
    const res = await fetch(SIM_API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = body.error || `Request failed with status ${res.status}`;
      throw new Error(msg);
    }

    state.result = body;
    state.lastRunProfile = {
      t: [...payload.t],
      p: [...payload.p],
    };
    renderPlots(body);
    updateStats();
  } catch (err) {
    state.result = null;
    state.lastRunProfile = null;
    updateStats();
    setError(err.message || String(err));
  } finally {
    el.runSpinner.classList.add("hidden");
    updateRunState();
  }
}

function applyGeneratedProfile(profile, meta = {}) {
  state.profile = profile;
  state.profileSource = meta.source || "manual";
  state.profileOnDuration = Number.isFinite(meta.onDuration) ? meta.onDuration : NaN;
  refreshProfileOnDurationMeta();
  renderProfileTable();
  updateRunState();
}

function bindEvents() {
  el.modelSelect.addEventListener("change", async () => {
    state.model = el.modelSelect.value;
    refreshMethodOptions();
    renderParamTable();
    await renderSchematic();
    updateRunState();
  });

  el.methodSelect.addEventListener("change", () => {
    state.method = el.methodSelect.value;
    updateMethodHelp();
  });

  el.overlayPowerToggle.addEventListener("change", () => {
    state.overlayPower = Boolean(el.overlayPowerToggle.checked);
    if (state.result) renderPlots(state.result);
  });
  el.exportCsvBtn.addEventListener("click", exportCsv);

  el.orderInput.addEventListener("change", async () => {
    const nextN = Number.parseInt(el.orderInput.value, 10);
    if (!Number.isInteger(nextN) || nextN < 1) {
      updateRunState();
      return;
    }
    resizeParamArrays(nextN);
    renderParamTable();
    await renderSchematic();
    updateRunState();
  });

  el.ambientInput.addEventListener("input", () => {
    state.ambient = ensureNumber(el.ambientInput.value);
    updateRunState();
  });

  el.profileDt.addEventListener("input", () => {
    state.profileDt = ensureNumber(el.profileDt.value);
    updateRunState();
  });

  el.simTimeInput.addEventListener("input", () => {
    state.simTotalTime = ensureNumber(el.simTimeInput.value);
    updateRunState();
  });

  el.applyPasteBtn.addEventListener("click", async () => {
    const rVals = parseNumberList(el.rthPaste.value);
    const cVals = parseNumberList(el.cthPaste.value);

    if (!rVals.length || !cVals.length) {
      setError("Paste both Rth and Cth lists.");
      return;
    }
    if (rVals.length !== cVals.length) {
      setError("Rth and Cth pasted lengths must match.");
      return;
    }
    if (rVals.some((v) => !Number.isFinite(v)) || cVals.some((v) => !Number.isFinite(v))) {
      setError("Pasted values must be numeric.");
      return;
    }

    setError("");
    state.N = rVals.length;
    state.Rth = rVals;
    state.Cth = cVals;
    state.branchLabels = Array.from({ length: state.N }, (_, i) => state.branchLabels[i] || "");
    el.orderInput.value = String(state.N);
    renderParamTable();
    await renderSchematic();
    updateRunState();
  });

  el.paramBody.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) {
      return;
    }
    if (target.classList.contains("param-input")) {
      const idx = Number.parseInt(target.dataset.index || "-1", 10);
      const kind = target.dataset.kind;
      const value = ensureNumber(target.value);
      if (idx < 0 || idx >= state.N) return;

      if (kind === "Rth") state.Rth[idx] = value;
      if (kind === "Cth") state.Cth[idx] = value;

      const row = target.closest("tr");
      const tauCell = row ? row.querySelector("td.readonly") : null;
      const tau = state.Rth[idx] * state.Cth[idx];
      if (tauCell) {
        tauCell.textContent = Number.isFinite(tau) ? tau.toFixed(6) : "-";
      }
      updateRunState();
      return;
    }

    if (target.classList.contains("param-label-input")) {
      const idx = Number.parseInt(target.dataset.index || "-1", 10);
      if (idx < 0 || idx >= state.N) return;
      state.branchLabels[idx] = target.value;
      if (state.result && state.model === "cauer") {
        renderPlots(state.result);
      }
      updateRunState();
    }
  });

  // Auto-fill RC table directly from clipboard paste in table cells.
  el.paramBody.addEventListener("paste", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) {
      return;
    }
    if (target.classList.contains("param-label-input")) {
      const text = event.clipboardData?.getData("text") || "";
      const lines = text.replace(/\r/g, "").split("\n");
      if (!lines.length) return;
      event.preventDefault();
      const startIdx = Number.parseInt(target.dataset.index || "0", 10);
      for (let i = 0; i < lines.length; i += 1) {
        const idx = startIdx + i;
        if (idx >= state.N) break;
        state.branchLabels[idx] = lines[i].trim();
      }
      renderParamTable();
      if (state.result && state.model === "cauer") {
        renderPlots(state.result);
      }
      updateRunState();
      return;
    }
    if (!target.classList.contains("param-input")) {
      return;
    }

    const text = event.clipboardData?.getData("text") || "";
    const grid = parsePastedGrid(text);
    if (!grid || !grid.length) {
      return;
    }

    event.preventDefault();

    const startIdx = Number.parseInt(target.dataset.index || "0", 10);
    const kind = target.dataset.kind;

    const hasMultiCol = grid.some((row) => row.length >= 2);

    if (hasMultiCol) {
      for (let r = 0; r < grid.length; r += 1) {
        const idx = startIdx + r;
        if (idx >= state.N) break;
        const row = grid[r];

        if (kind === "Rth") {
          if (Number.isFinite(row[0])) state.Rth[idx] = row[0];
          if (Number.isFinite(row[1])) state.Cth[idx] = row[1];
        } else {
          const val = row.length > 1 ? row[row.length - 1] : row[0];
          if (Number.isFinite(val)) state.Cth[idx] = val;
        }
      }
    } else {
      const flat = grid.flat();
      for (let i = 0; i < flat.length; i += 1) {
        const idx = startIdx + i;
        if (idx >= state.N) break;
        if (kind === "Rth") state.Rth[idx] = flat[i];
        if (kind === "Cth") state.Cth[idx] = flat[i];
      }
    }

    renderParamTable();
    updateRunState();
  });

  el.profileBody.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || !target.classList.contains("profile-input")) {
      return;
    }
    const idx = Number.parseInt(target.dataset.index || "-1", 10);
    const kind = target.dataset.kind;
    const value = ensureNumber(target.value);
    if (idx < 0 || idx >= state.profile.t.length) return;

    if (kind === "t") state.profile.t[idx] = value;
    if (kind === "p") state.profile.p[idx] = value;
    refreshProfileOnDurationMeta();
    updateRunState();
  });

  el.profileBody.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLButtonElement) || !target.classList.contains("remove-row-btn")) {
      return;
    }
    if (state.profile.t.length <= 2) {
      setError("Profile must keep at least two rows.");
      return;
    }
    const idx = Number.parseInt(target.dataset.index || "-1", 10);
    if (idx < 0 || idx >= state.profile.t.length) return;

    state.profile.t.splice(idx, 1);
    state.profile.p.splice(idx, 1);
    refreshProfileOnDurationMeta();
    renderProfileTable();
    updateRunState();
  });

  el.addProfileRowBtn.addEventListener("click", () => {
    const m = state.profile.t.length;
    const nextT = m > 0 ? state.profile.t[m - 1] + state.profileDt : 0;
    state.profile.t.push(Number(nextT.toFixed(12)));
    state.profile.p.push(m > 0 ? state.profile.p[m - 1] : 0);
    refreshProfileOnDurationMeta();
    renderProfileTable();
    updateRunState();
  });

  el.sortProfileBtn.addEventListener("click", () => {
    const zipped = state.profile.t.map((tVal, i) => ({ t: tVal, p: state.profile.p[i] }));
    zipped.sort((a, b) => a.t - b.t);
    state.profile.t = zipped.map((v) => v.t);
    state.profile.p = zipped.map((v) => v.p);
    refreshProfileOnDurationMeta();
    renderProfileTable();
    updateRunState();
  });

  el.uploadProfileCsvBtn.addEventListener("click", () => {
    setError("");
    setProfileUploadHint(
      "CSV format: either no header with first two columns as t, P; or a header row containing columns named t and P. Time must be strictly increasing."
    );
    if (!el.uploadProfileCsvInput) return;
    el.uploadProfileCsvInput.value = "";
    el.uploadProfileCsvInput.click();
  });

  el.uploadProfileCsvInput.addEventListener("change", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || !target.files || !target.files.length) {
      return;
    }

    try {
      const file = target.files[0];
      const text = await file.text();
      const profile = parseProfileCsv(text);
      setError("");
      setProfileUploadHint(`Loaded ${profile.t.length} rows from ${file.name}.`);
      applyGeneratedProfile(profile, { source: "csv" });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
    }
  });

  el.genPulseBtn.addEventListener("click", () => {
    const period = ensureNumber(el.pulsePeriod.value);
    const power = ensureNumber(el.pulsePower.value);
    const onDuration = ensureNumber(el.pulseOnDuration.value);

    const dt = state.profileDt;
    const total = state.simTotalTime;

    if (!(Number.isFinite(period) && Number.isFinite(power) && Number.isFinite(onDuration))) {
      setError("Pulse generator inputs must be numeric.");
      return;
    }
    if (!Number.isFinite(dt) || dt <= 0 || !Number.isFinite(total) || total <= 0) {
      setError("dt and total simulation time must be positive.");
      return;
    }
    if (period <= 0 || onDuration <= 0 || onDuration > period) {
      setError("Pulse generator requires period>0 and 0<on-duration<=period.");
      return;
    }

    setError("");
    applyGeneratedProfile(generatePulseProfile(period, power, onDuration, dt, total), {
      source: "pulse",
      onDuration,
    });
  });

  el.genStepBtn.addEventListener("click", () => {
    const power = ensureNumber(el.stepPower.value);
    const dt = state.profileDt;
    const total = state.simTotalTime;

    if (!Number.isFinite(power) || !Number.isFinite(dt) || !Number.isFinite(total)) {
      setError("Step generator inputs must be numeric.");
      return;
    }
    if (dt <= 0 || total <= 0) {
      setError("Step generator requires dt>0 and total simulation time>0.");
      return;
    }

    setError("");
    applyGeneratedProfile(generateStepProfile(power, dt, total), { source: "step" });
  });

  el.runBtn.addEventListener("click", runSimulation);
}

function setDefaultProfile() {
  state.profile = generatePulseProfile(0.2, 100.0, 0.1, state.profileDt, state.simTotalTime);
  state.profileSource = "pulse";
  state.profileOnDuration = 0.1;
  refreshProfileOnDurationMeta();
}

async function bootstrap() {
  initElements();
  setDefaultProfile();

  el.modelSelect.value = state.model;
  el.orderInput.value = String(state.N);
  el.ambientInput.value = String(state.ambient);
  el.overlayPowerToggle.checked = state.overlayPower;
  el.exportCsvBtn.disabled = true;
  el.profileDt.value = String(state.profileDt);
  el.simTimeInput.value = String(state.simTotalTime);

  refreshMethodOptions();
  renderParamTable();
  renderProfileTable();
  await renderSchematic();
  updateRunState();
  bindEvents();
}

document.addEventListener("DOMContentLoaded", () => {
  bootstrap().catch((err) => {
    setError(err.message || String(err));
  });
});
