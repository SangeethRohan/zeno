const API_PREFIX = "/api/v1";

let containers = [];
const openRows = new Set();
const openLogs = new Set();
let selectedEngine = "postgres";
let tableCount = 0;

const HISTORY_LEN = 40;
const SPARK_SMOOTHING = 0.14;
const SPARK_TAIL_SMOOTHING = 0.22;
const HOST_POLL_MS = 1000;
const STATS_POLL_OPEN_MS = 1000;
const STATS_POLL_CLOSED_MS = 30000;
const STATS_TICK_MS = 1000;
const cpuHistory = [];
const memHistory = [];
const diskHistory = [];
const cpuDisplay = [];
const memDisplay = [];
const diskDisplay = [];
let sparkAnimId = null;
const statsCache = new Map();
const statsLastFetched = new Map();
const statsInFlight = new Set();
const metricHistoryCache = new Map();
const containerLiveHistory = new Map();
const CONTAINER_LIVE_MAX = 180;
let hostChartMode = "live";
let hostHistory24Points = [];
let containerListKey = "";
let hostStatsTimer = null;
let containerRefreshTimer = null;
let statsRefreshTimer = null;
let openStatsTimer = null;
let alertsTimer = null;
let logsAutoTimer = null;
let resizeRaf = null;
let timelineFilter = "all";
let alertFilter = "all";

const loadedLogs = new Set();

const COL_TYPES = ["TEXT", "VARCHAR(255)", "INTEGER", "BIGINT", "BOOLEAN", "TIMESTAMP", "DECIMAL(10,2)"];

const $ = id => document.getElementById(id);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "include",
    ...opts
  });

  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    if (res.status === 401) {
      location.href = "/login";
      return;
    }
    throw new Error(data.error || "Request failed");
  }

  return data;
}

/* ---------------- Navigation ---------------- */

let currentUser = { is_admin: false };
let alertNotificationPrefs = {
  cpu_high: true,
  mem_high: true,
  crash_loop: true,
  port_failure: true
};

function alertsVisibleOnDashboard(alerts) {
  return (alerts || []).filter(a => alertNotificationPrefs[a.rule] !== false);
}

const VIEW_TITLES = {
  dashboard: ["Dashboard", "local dev environment"],
  host: ["Host", "system information and resources"],
  create: ["Create", "spin up databases, servers, and web services"],
  timeline: ["Timeline", "what happened in the last 24 hours"],
  logs: ["Central Logs", "your activity and container logs"],
  alerts: ["Alerts", "container CPU, memory, crash loop, and port alerts"],
  "create-db": ["Create Database", "spin up a new persistent db container"],
  "create-ubuntu": ["Create Ubuntu Server", "dev sandbox with optional languages"],
  "create-web": ["Create Web Server", "nginx, apache, caddy, or traefik"],
  "manage-groups": ["Manage Groups", "drag containers between your custom categories"],
  "tier-features": ["Tier Features", "enable features per Core, Pro, and Elite"]
};

const terminalHistory = new Map();
const terminalCwd = new Map();
const terminalShell = new Map();
let terminalContainer = null;
let selectedWebType = "nginx";
let groupLayout = null;
let groupsEditorData = null;
let dragPayload = null;
let tierFeaturesData = null;

document.querySelectorAll(".nav-item").forEach(item => {
  item.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach(i => i.classList.remove("active"));
    item.classList.add("active");
    const view = item.dataset.view;
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    $(`view-${view}`).classList.add("active");
    const [title, sub] = VIEW_TITLES[view] || ["Zeno", ""];
    $("page-title").textContent = title;
    $("page-sub").textContent = sub;
    if (view === "host") {
      refreshHostDetails();
      renderHostCharts();
    }
    if (view === "create") applyCreateFeatureCards();
    if (view === "timeline") loadTimeline();
    if (view === "logs") loadCentralLogsPage();
    if (view === "alerts") loadAlertsPage();
    if (view === "manage-groups") loadGroupsEditor();
    if (view === "tier-features") loadTierFeaturesEditor();
  });
});

$("profile-chip")?.addEventListener("click", e => {
  e.stopPropagation();
  const menu = $("profile-menu");
  const dropdown = $("profile-dropdown");
  const open = menu.classList.toggle("open");
  dropdown.hidden = !open;
  $("profile-chip").setAttribute("aria-expanded", open ? "true" : "false");
});

document.addEventListener("click", e => {
  if (!$("profile-menu")?.contains(e.target)) {
    $("profile-menu")?.classList.remove("open");
    if ($("profile-dropdown")) $("profile-dropdown").hidden = true;
    $("profile-chip")?.setAttribute("aria-expanded", "false");
  }
});

$("logout-btn")?.addEventListener("click", async () => {
  try {
    await api(`${API_PREFIX}/logout`, { method: "POST" });
  } finally {
    location.href = "/login";
  }
});

/* ---------------- Profile ---------------- */

function applyTierBadge(tier) {
  const badge = $("tier-badge");
  if (!badge) return;
  badge.textContent = tier;
  badge.className = "tier-badge";
  badge.classList.add(`tier-${tier.toLowerCase()}`);
}

async function loadProfile() {
  try {
    const p = await api(`${API_PREFIX}/me`);
    currentUser = p;
    if (p.alert_notifications) {
      alertNotificationPrefs = { ...alertNotificationPrefs, ...p.alert_notifications };
    }
    $("profile-name").textContent = p.username;
    $("profile-host").textContent = p.host;
    $("avatar-initials").textContent = p.username.slice(0, 2).toUpperCase();
    if (p.tier) applyTierBadge(p.tier);
    const manageLink = $("manage-users-link");
    if (manageLink) {
      if (p.is_admin) {
        manageLink.hidden = false;
      } else {
        manageLink.remove();
      }
    }
    const tierNav = $("nav-tier-features");
    if (tierNav) tierNav.hidden = !p.is_admin;
    applyFeatureNav(p.features || {});
  } catch {
    $("profile-name").textContent = "unknown";
  }
}

function applyFeatureNav(features) {
  const createNav = $("nav-create");
  const hasAnyCreate = Boolean(
    features.create_database || features.create_ubuntu || features.create_web_server
  );
  if (createNav) createNav.hidden = !hasAnyCreate;

  const cardMap = {
    create_database: "create-card-database",
    create_ubuntu: "create-card-ubuntu",
    create_web_server: "create-card-web"
  };
  Object.entries(cardMap).forEach(([key, id]) => {
    const el = $(id);
    if (el) el.hidden = !features[key];
  });
}

function navigateToView(view) {
  const navItem = document.querySelector(`.nav-item[data-view="${view}"]`);
  if (navItem) {
    navItem.click();
    return;
  }
  document.querySelectorAll(".nav-item").forEach(i => i.classList.remove("active"));
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  $(`view-${view}`)?.classList.add("active");
  const [title, sub] = VIEW_TITLES[view] || ["Zeno", ""];
  $("page-title").textContent = title;
  $("page-sub").textContent = sub;
}

function applyCreateFeatureCards() {
  const features = currentUser.features || {};
  applyFeatureNav(features);
}

const CREATE_TYPE_LABELS = {
  database: "Database containers",
  ubuntu: "Ubuntu servers",
  web: "Web servers"
};

function filterContainersByCreateType(type) {
  return (containers || []).filter(c => {
    if (type === "database") return c.is_user_db;
    if (type === "ubuntu") return c.is_user_server;
    if (type === "web") return c.is_user_web;
    return false;
  });
}

function renderCreateAvailableList(type) {
  const panel = $("create-available-panel");
  const list = $("create-available-list");
  const title = $("create-available-title");
  if (!panel || !list) return;

  const matches = filterContainersByCreateType(type);
  if (title) title.textContent = CREATE_TYPE_LABELS[type] || "Available containers";

  if (!matches.length) {
    list.innerHTML = '<div class="empty">No containers found for this template.</div>';
  } else {
    list.innerHTML = matches.map(c => `
      <div class="create-available-item">
        <div>
          <div>${escapeHtml(c.name)}</div>
          <div class="create-available-meta">${escapeHtml(c.image || "")}${c.engine ? ` · ${escapeHtml(c.engine)}` : ""}</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;">
          <span class="status-dot ${escapeHtml(statusClass(c))}"></span>
          <span>${escapeHtml(c.status)}</span>
        </div>
      </div>
    `).join("");
  }
  panel.hidden = false;
}

document.querySelectorAll("[data-create-action]").forEach(btn => {
  btn.addEventListener("click", () => {
    const type = btn.dataset.createType;
    const action = btn.dataset.createAction;
    if (!type) return;
    if (action === "show") {
      renderCreateAvailableList(type);
      $("create-available-panel")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return;
    }
    if (action === "create") {
      const viewMap = {
        database: "create-db",
        ubuntu: "create-ubuntu",
        web: "create-web"
      };
      navigateToView(viewMap[type]);
    }
  });
});

$("create-available-close")?.addEventListener("click", () => {
  const panel = $("create-available-panel");
  if (panel) panel.hidden = true;
});

/* ---------------- Confirm dialog ---------------- */

let confirmResolver = null;

function showConfirm(message, options = {}) {
  const modal = $("confirm-modal");
  const msgEl = $("confirm-message");
  const okBtn = $("confirm-ok");
  const titleEl = $("confirm-title");
  if (!modal || !msgEl || !okBtn) {
    return Promise.resolve(window.confirm(message));
  }
  if (confirmResolver) {
    confirmResolver(false);
    confirmResolver = null;
  }
  if (titleEl) titleEl.textContent = options.title || "Confirm";
  msgEl.textContent = message;
  okBtn.textContent = options.confirmLabel || "Delete";
  modal.hidden = false;
  return new Promise(resolve => {
    confirmResolver = resolve;
  });
}

function closeConfirm(result) {
  const modal = $("confirm-modal");
  if (modal) modal.hidden = true;
  if (confirmResolver) {
    confirmResolver(result);
    confirmResolver = null;
  }
}

$("confirm-cancel")?.addEventListener("click", () => closeConfirm(false));
$("confirm-backdrop")?.addEventListener("click", () => closeConfirm(false));
$("confirm-ok")?.addEventListener("click", () => closeConfirm(true));

/* ---------------- Dashboard ---------------- */

function showError(msg) {
  const el = $("error");
  if (!msg) {
    el.style.display = "none";
    return;
  }
  el.textContent = msg;
  el.style.display = "block";
}

function statusClass(c) {
  if (c.status === "running") return "running";
  if (c.status === "paused" || c.status === "restarting") return c.status;
  return "exited";
}

function fmtSince(iso) {
  if (!iso || iso.startsWith("0001")) return "—";
  const d = new Date(iso);
  const mins = Math.floor((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return mins + "m";
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + "h";
  return Math.floor(hrs / 24) + "d";
}

function setText(id, text) {
  const el = $(id);
  if (el && el.textContent !== text) el.textContent = text;
}

function setStatText(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
}

function formatStatsDisplay(s) {
  return {
    cpu: `${Number(s.cpu).toFixed(3)}%`,
    mem: s.memory,
    net: `↓ ${s.network_rx} ↑ ${s.network_tx}`,
    disk: `R ${s.block_read} W ${s.block_write}`
  };
}

function cachedStatTexts(name) {
  const cached = statsCache.get(name);
  if (cached) return formatStatsDisplay(cached);
  const c = containers.find(x => x.name === name);
  if (c && c.status !== "running") {
    return {
      cpu: "0.000%",
      mem: "Stopped",
      net: "↓ - ↑ -",
      disk: "R - W -"
    };
  }
  return { cpu: "…", mem: "…", net: "…", disk: "…" };
}

function statsDomExists(name) {
  return Boolean($(`cpu-${name}`));
}

function shouldRefreshStats(name) {
  const isOpen = openRows.has(name);
  const interval = isOpen ? STATS_POLL_OPEN_MS : STATS_POLL_CLOSED_MS;
  const last = statsLastFetched.get(name) || 0;
  return Date.now() - last >= interval;
}

function primeContainerStats() {
  for (const c of containers) {
    if (c.status !== "running") continue;
    if (!statsCache.has(c.name) || shouldRefreshStats(c.name)) {
      loadStats(c.name, { force: !statsCache.has(c.name) });
    }
  }
}

function applyStatsToDom(name, s) {
  const d = formatStatsDisplay(s);
  setStatText(`cpu-${name}`, d.cpu);
  setStatText(`mem-${name}`, d.mem);
  setStatText(`net-${name}`, d.net);
  setStatText(`disk-${name}`, d.disk);
}

function containersListKey(list) {
  return list.map(c =>
    `${c.name}|${c.status}|${c.image}|${c.started_at}|${c.ports.join(",")}|${c.group}|${c.group_id || ""}`
  ).join("\n");
}

function hexToRgba(hex, alpha) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function sparkPoints(data, w, h, max = 100) {
  const pad = 4;
  const stepX = (w - pad * 2) / (HISTORY_LEN - 1);
  const startIdx = HISTORY_LEN - data.length;
  return data.map((v, i) => {
    const x = pad + (startIdx + i) * stepX;
    const y = h - pad - (Math.min(Math.max(v, 0), max) / max) * (h - pad * 2);
    return [x, y];
  });
}

function traceSmoothPath(ctx, points) {
  if (points.length < 2) return;
  ctx.moveTo(points[0][0], points[0][1]);
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] || points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;
    const cp1x = p1[0] + (p2[0] - p0[0]) / 6;
    const cp1y = p1[1] + (p2[1] - p0[1]) / 6;
    const cp2x = p2[0] - (p3[0] - p1[0]) / 6;
    const cp2y = p2[1] - (p3[1] - p1[1]) / 6;
    ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2[0], p2[1]);
  }
}

function drawSpark(canvas, data, color, max = 100) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (w === 0 || h === 0) return;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (data.length < 2) return;

  const pad = 4;
  const points = sparkPoints(data, w, h, max);
  const baseline = h - pad;

  ctx.beginPath();
  ctx.moveTo(points[0][0], baseline);
  traceSmoothPath(ctx, points);
  ctx.lineTo(points[points.length - 1][0], baseline);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, pad, 0, h - pad);
  grad.addColorStop(0, hexToRgba(color, 0.28));
  grad.addColorStop(0.55, hexToRgba(color, 0.1));
  grad.addColorStop(1, hexToRgba(color, 0));
  ctx.fillStyle = grad;
  ctx.fill();

  ctx.beginPath();
  traceSmoothPath(ctx, points);
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.stroke();

  const [lx, ly] = points[points.length - 1];
  ctx.beginPath();
  ctx.arc(lx, ly, 3, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.beginPath();
  ctx.arc(lx, ly, 6, 0, Math.PI * 2);
  ctx.fillStyle = color + "33";
  ctx.fill();
}

function pushSparkHistory(target, display, val) {
  const lastDisplay = display.length ? display[display.length - 1] : val;
  target.push(val);
  display.push(lastDisplay);
  if (target.length > HISTORY_LEN) {
    target.shift();
    display.shift();
  }
}

function lerpSparkDisplay(target, display) {
  while (display.length < target.length) {
    const i = display.length;
    display.push(target[i]);
  }
  while (display.length > target.length) display.shift();
  for (let i = 0; i < target.length; i++) {
    const rate = i === target.length - 1 ? SPARK_TAIL_SMOOTHING : SPARK_SMOOTHING;
    display[i] += (target[i] - display[i]) * rate;
  }
}

function renderSparklines() {
  if (hostChartMode !== "live") return;
  lerpSparkDisplay(cpuHistory, cpuDisplay);
  lerpSparkDisplay(memHistory, memDisplay);
  lerpSparkDisplay(diskHistory, diskDisplay);
  drawSpark($("cpu-chart"), cpuDisplay, "#4FC3D9");
  drawSpark($("mem-chart"), memDisplay, "#A684FF");
  drawSpark($("disk-chart"), diskDisplay, "#F5A623");
}

function hostMemPercent(p) {
  if (p.mem_percent != null) return Number(p.mem_percent);
  return p.mem_limit_mb ? (p.mem_used_mb / p.mem_limit_mb) * 100 : 0;
}

function hostDiskPercent(p) {
  return p.block_write_bytes
    ? (p.block_read_bytes / p.block_write_bytes) * 100
    : 0;
}

async function loadHost24hCharts() {
  try {
    const data = await api(`${API_PREFIX}/metrics/history?container=__host__&hours=24`);
    hostHistory24Points = data.points || [];
    if (hostHistory24Points.length) {
      const last = hostHistory24Points[hostHistory24Points.length - 1];
      setText("cpu-value", `${Number(last.cpu).toFixed(1)}%`);
      setText("mem-value", `${hostMemPercent(last).toFixed(1)}%`);
      setText("disk-value", `${hostDiskPercent(last).toFixed(1)}%`);
    }
    wireHistoryChart(
      "cpu-chart", "host-cpu-tooltip", hostHistory24Points, "#4FC3D9",
      p => Number(p.cpu || 0), "CPU"
    );
    wireHistoryChart(
      "mem-chart", "host-mem-tooltip", hostHistory24Points, "#A684FF",
      p => hostMemPercent(p), "Memory"
    );
    wireHistoryChart(
      "disk-chart", "host-disk-tooltip", hostHistory24Points, "#F5A623",
      p => hostDiskPercent(p), "Disk"
    );
  } catch (e) {
    console.error("host 24h charts", e);
  }
}

function renderHostCharts() {
  if (hostChartMode === "live") {
    renderSparklines();
    return;
  }
  loadHost24hCharts();
}

function startSparkAnimation() {
  if (sparkAnimId) cancelAnimationFrame(sparkAnimId);
  const tick = () => {
    renderSparklines();
    sparkAnimId = requestAnimationFrame(tick);
  };
  sparkAnimId = requestAnimationFrame(tick);
}

async function refreshHostStats() {
  try {
    const s = await api(`${API_PREFIX}/host/stats`);
    pushSparkHistory(cpuHistory, cpuDisplay, s.cpu_percent);
    pushSparkHistory(memHistory, memDisplay, s.mem_percent);
    pushSparkHistory(diskHistory, diskDisplay, s.disk_percent);
    setText("cpu-value", s.cpu_percent.toFixed(1) + "%");
    const la = s.load_avg && s.load_avg[0] != null
      ? s.load_avg.map(v => v.toFixed(2)).join(" / ")
      : "—";
    setText("cpu-sub", `load avg (1/5/15m): ${la}`);
    setText("mem-value", s.mem_percent.toFixed(1) + "%");
    setText("mem-sub", `${s.mem_used_gb} GB used / ${s.mem_total_gb} GB total`);
    setText("disk-value", s.disk_percent.toFixed(1) + "%");
    setText("disk-sub", `${s.disk_used_gb} GB used / ${s.disk_total_gb} GB total`);
  } catch (e) {
    console.error("host stats error", e);
  }
}

function ensureMinChartPoints(points) {
  if (!points.length) return points;
  if (points.length === 1) return [points[0], { ...points[0] }];
  return points;
}

function pushContainerLivePoint(name, s) {
  if (!s || s.memory === "Stopped") return;
  let arr = containerLiveHistory.get(name);
  if (!arr) {
    arr = [];
    containerLiveHistory.set(name, arr);
  }
  let memPct = s.mem_percent;
  if (memPct == null && s.mem_used_mb && s.mem_limit_mb) {
    memPct = (s.mem_used_mb / s.mem_limit_mb) * 100;
  }
  arr.push({
    ts: new Date().toISOString(),
    cpu: Number(s.cpu) || 0,
    mem_percent: Number(memPct) || 0,
    block_read_bytes: Number(s.block_read_bytes) || 0,
    block_write_bytes: Number(s.block_write_bytes) || 0,
  });
  if (arr.length > CONTAINER_LIVE_MAX) arr.shift();
}

function mergeContainerChartPoints(name) {
  const historical = metricHistoryCache.get(name) || [];
  const live = containerLiveHistory.get(name) || [];
  if (!historical.length) return live;
  const lastHistTs = historical[historical.length - 1].ts || "";
  const liveOnly = live.filter(p => (p.ts || "") > lastHistTs);
  return [...historical, ...liveOnly];
}

function containerDiskSeries(points) {
  const pts = ensureMinChartPoints(points);
  if (!pts.length) return { data: [], max: 1 };
  let prevRead = pts[0].block_read_bytes || 0;
  const data = pts.map(p => {
    const rate = Math.max(0, (p.block_read_bytes || 0) - prevRead);
    prevRead = p.block_read_bytes || 0;
    return rate / 1024 / 1024;
  });
  return { data, max: Math.max(...data, 0.1) };
}

async function loadContainerMetricHistory(name) {
  try {
    const data = await api(`${API_PREFIX}/metrics/history?container=${encodeURIComponent(name)}&hours=24`);
    metricHistoryCache.set(name, data.points || []);
    renderContainerMetricCharts(name);
  } catch (e) {
    console.error("metric history", name, e);
    renderContainerMetricCharts(name);
  }
}

function renderContainerMetricCharts(name) {
  const wrap = $(`metric-charts-${name}`);
  if (!wrap) return;
  const points = ensureMinChartPoints(mergeContainerChartPoints(name));
  if (!points.length) return;

  wireHistoryChart(
    `metric-cpu-${name}`, `metric-cpu-tip-${name}`, points, "#4FC3D9",
    p => Number(p.cpu || 0), "CPU"
  );
  wireHistoryChart(
    `metric-mem-${name}`, `metric-mem-tip-${name}`, points, "#A684FF",
    p => Number(p.mem_percent || 0), "Memory"
  );
  const disk = containerDiskSeries(points);
  const diskPoints = points.map((p, i) => ({ ...p, _diskRate: disk.data[i] ?? 0 }));
  wireHistoryChart(
    `metric-disk-${name}`, `metric-disk-tip-${name}`, diskPoints, "#F5A623",
    p => p._diskRate, "Disk I/O", disk.max, " MB/s"
  );
}

document.querySelectorAll("#host-chart-mode .filter-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#host-chart-mode .filter-chip").forEach(c =>
      c.classList.remove("active")
    );
    chip.classList.add("active");
    hostChartMode = chip.dataset.hostMode || "live";
    renderHostCharts();
  });
});

function fmtUptime(seconds) {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

async function refreshHostDetails() {
  const el = $("host-details");
  const err = $("host-error");
  if (!el) return;
  try {
    const h = await api(`${API_PREFIX}/host/details`);
    if (err) err.style.display = "none";
    const load = h.load_avg?.filter(v => v != null).map(v => v.toFixed(2)).join(" / ") || "—";
    el.innerHTML = `
      <div class="host-detail-card">
        <div class="host-detail-label">System</div>
        <div class="host-detail-row"><span>Hostname</span><span>${h.hostname}</span></div>
        <div class="host-detail-row"><span>Platform</span><span>${h.platform} ${h.platform_release}</span></div>
        <div class="host-detail-row"><span>Architecture</span><span>${h.architecture}</span></div>
        <div class="host-detail-row"><span>Uptime</span><span>${fmtUptime(h.uptime_seconds)}</span></div>
        <div class="host-detail-row"><span>Boot time</span><span>${new Date(h.boot_time).toLocaleString()}</span></div>
      </div>
      <div class="host-detail-card">
        <div class="host-detail-label">Compute</div>
        <div class="host-detail-row"><span>CPU cores</span><span>${h.cpu_count} physical / ${h.cpu_count_logical} logical</span></div>
        <div class="host-detail-row"><span>CPU usage</span><span>${h.cpu_percent.toFixed(1)}%</span></div>
        <div class="host-detail-row"><span>Load avg</span><span>${load}</span></div>
        <div class="host-detail-row"><span>Memory</span><span>${h.mem_used_gb} / ${h.mem_total_gb} GB (${h.mem_percent}%)</span></div>
        <div class="host-detail-row"><span>Disk</span><span>${h.disk_used_gb} / ${h.disk_total_gb} GB (${h.disk_percent}%)</span></div>
      </div>
      <div class="host-detail-card">
        <div class="host-detail-label">Runtime</div>
        <div class="host-detail-row"><span>Python</span><span>${h.python_version}</span></div>
        <div class="host-detail-row"><span>Docker</span><span>${h.docker_version}</span></div>
        <div class="host-detail-row"><span>Containers</span><span>${h.container_count}</span></div>
      </div>`;
  } catch (e) {
    if (err) {
      err.textContent = "Could not load host details: " + e.message;
      err.style.display = "block";
    }
    el.innerHTML = '<div class="empty">Host details unavailable.</div>';
  }
}

async function refreshContainers() {
  try {
    const data = await api(`${API_PREFIX}/containers`);
    showError(null);
    await loadGroupLayout();
    const nextKey = containersListKey(data);
    containers = data;
    if (nextKey !== containerListKey) {
      containerListKey = nextKey;
      render();
    } else {
      patchContainerRows();
    }
    primeContainerStats();
    if ($("view-logs")?.classList.contains("active")) {
      populateLogsDropdowns();
    }
  } catch (e) {
    showError("Could not reach the dashboard backend: " + e.message);
  }
}

async function loadGroupLayout() {
  try {
    const data = await api(`${API_PREFIX}/groups/layout`);
    groupLayout = data.layout;
    if (groupsEditorData) groupsEditorData.layout = data.layout;
  } catch (e) {
    console.warn("group layout:", e);
  }
}

function showGroupsBanner(id, msg) {
  const el = $(id);
  if (!el) return;
  ["groups-error", "groups-success"].forEach(x => {
    if ($(x) && x !== id) $(x).style.display = "none";
  });
  if (!msg) {
    el.style.display = "none";
    return;
  }
  el.textContent = msg;
  el.style.display = "block";
}

function containersInGroup(layout, containers, groupId) {
  const inGroup = containers.filter(c => layout.assignments[c.name] === groupId);
  const order = layout.container_order?.[groupId] || [];
  return inGroup.sort((a, b) => {
    const ai = order.indexOf(a.name);
    const bi = order.indexOf(b.name);
    if (ai === -1 && bi === -1) return a.name.localeCompare(b.name);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });
}

function moveContainerInLayout(name, toGroupId, beforeName = null) {
  const layout = groupsEditorData.layout;
  if (!layout.container_order) layout.container_order = {};
  layout.assignments[name] = toGroupId;
  for (const gid of Object.keys(layout.container_order)) {
    layout.container_order[gid] = (layout.container_order[gid] || []).filter(n => n !== name);
  }
  if (!layout.container_order[toGroupId]) layout.container_order[toGroupId] = [];
  const list = layout.container_order[toGroupId];
  if (beforeName && beforeName !== name) {
    const idx = list.indexOf(beforeName);
    if (idx >= 0) list.splice(idx, 0, name);
    else list.push(name);
  } else if (!list.includes(name)) {
    list.push(name);
  }
}

function renderGroupsEditor() {
  const editor = $("groups-editor");
  if (!groupsEditorData?.layout) {
    editor.innerHTML = '<div class="empty">No layout loaded.</div>';
    return;
  }

  const layout = groupsEditorData.layout;
  const containers = groupsEditorData.containers || [];
  const sortedGroups = [...layout.groups].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));

  editor.innerHTML = sortedGroups.map(g => {
    const items = containersInGroup(layout, containers, g.id);
    const chips = items.map(c => `
      <div class="group-container-chip ${c.is_core_app ? "locked" : ""}"
           data-container="${escapeHtml(c.name)}"
           ${c.is_core_app ? "" : 'draggable="true"'}>
        <span class="chip-grip">${c.is_core_app ? "🔒" : "⋮"}</span>
        <span>${escapeHtml(c.name)}</span>
      </div>
    `).join("");

    return `
      <div class="group-editor-card ${g.locked ? "locked" : ""}"
           data-group-id="${escapeHtml(g.id)}">
        <div class="group-editor-head">
          <span class="drag-handle" draggable="${g.locked ? "false" : "true"}" data-drag-group="${escapeHtml(g.id)}" title="${g.locked ? "Locked" : "Drag to reorder"}">${g.locked ? "🔒" : "⋮⋮"}</span>
          <span class="group-editor-name">${escapeHtml(g.name)}</span>
          ${g.locked ? "" : `<button type="button" class="small-btn danger" data-del-group="${escapeHtml(g.id)}">Delete</button>`}
        </div>
        <div class="group-drop-zone" data-group-id="${escapeHtml(g.id)}">
          ${items.length ? chips : '<div class="group-drop-empty">Drop containers here</div>'}
        </div>
      </div>
    `;
  }).join("");

  wireGroupsEditorEvents();
}

function wireGroupsEditorEvents() {
  document.querySelectorAll(".group-container-chip[draggable=true]").forEach(chip => {
    chip.addEventListener("dragstart", e => {
      e.stopPropagation();
      dragPayload = { type: "container", name: chip.dataset.container };
      e.dataTransfer.setData("text/plain", chip.dataset.container);
      e.dataTransfer.effectAllowed = "move";
      chip.classList.add("dragging");
    });
    chip.addEventListener("dragend", () => {
      chip.classList.remove("dragging");
      dragPayload = null;
    });
    chip.addEventListener("dragover", e => {
      if (dragPayload?.type === "container") {
        e.preventDefault();
        e.stopPropagation();
        chip.classList.add("drag-over-chip");
      }
    });
    chip.addEventListener("dragleave", () => chip.classList.remove("drag-over-chip"));
    chip.addEventListener("drop", e => {
      e.preventDefault();
      e.stopPropagation();
      chip.classList.remove("drag-over-chip");
      if (dragPayload?.type !== "container") return;
      const c = groupsEditorData.containers.find(x => x.name === dragPayload.name);
      if (!c || c.is_core_app) return;
      const zone = chip.closest(".group-drop-zone");
      const gid = zone?.dataset.groupId;
      if (!gid) return;
      moveContainerInLayout(dragPayload.name, gid, chip.dataset.container);
      renderGroupsEditor();
    });
  });

  document.querySelectorAll(".drag-handle[draggable=true]").forEach(handle => {
    handle.addEventListener("dragstart", e => {
      e.stopPropagation();
      dragPayload = { type: "group", id: handle.dataset.dragGroup };
      e.dataTransfer.setData("text/plain", "group:" + handle.dataset.dragGroup);
      e.dataTransfer.effectAllowed = "move";
      handle.closest(".group-editor-card")?.classList.add("dragging");
    });
    handle.addEventListener("dragend", () => {
      document.querySelectorAll(".group-editor-card.dragging").forEach(c => c.classList.remove("dragging"));
      dragPayload = null;
    });
  });

  document.querySelectorAll(".group-drop-zone").forEach(zone => {
    zone.addEventListener("dragover", e => {
      e.preventDefault();
      zone.classList.add("drag-over");
    });
    zone.addEventListener("dragleave", e => {
      if (!zone.contains(e.relatedTarget)) zone.classList.remove("drag-over");
    });
    zone.addEventListener("drop", e => {
      e.preventDefault();
      zone.classList.remove("drag-over");
      if (!dragPayload) return;

      if (dragPayload.type === "container") {
        const c = groupsEditorData.containers.find(x => x.name === dragPayload.name);
        if (!c || c.is_core_app) return;
        moveContainerInLayout(dragPayload.name, zone.dataset.groupId);
        renderGroupsEditor();
        return;
      }

      if (dragPayload.type === "group") {
        const targetId = zone.dataset.groupId;
        const groups = [...groupsEditorData.layout.groups].sort((a, b) => a.order - b.order);
        const fromIdx = groups.findIndex(g => g.id === dragPayload.id);
        const toIdx = groups.findIndex(g => g.id === targetId);
        if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return;
        const [moved] = groups.splice(fromIdx, 1);
        groups.splice(toIdx, 0, moved);
        groups.forEach((g, i) => { g.order = i; });
        groupsEditorData.layout.groups = groups;
        renderGroupsEditor();
      }
    });
  });

  document.querySelectorAll("[data-del-group]").forEach(btn => {
    btn.addEventListener("click", async e => {
      e.stopPropagation();
      const gid = btn.dataset.delGroup;
      if (!await showConfirm("Delete this group? Containers will move to another group.", {
        title: "Delete group",
        confirmLabel: "Delete"
      })) return;
      try {
        const res = await api(`${API_PREFIX}/groups/${encodeURIComponent(gid)}`, { method: "DELETE" });
        groupsEditorData.layout = res.layout;
        groupLayout = res.layout;
        renderGroupsEditor();
        showGroupsBanner("groups-success", "Group deleted.");
      } catch (err) {
        showGroupsBanner("groups-error", err.message);
      }
    });
  });
}

async function loadGroupsEditor() {
  showGroupsBanner("groups-error", null);
  showGroupsBanner("groups-success", null);
  $("groups-editor").innerHTML = '<div class="empty">Loading groups…</div>';
  try {
    const data = await api(`${API_PREFIX}/groups/layout`);
    groupsEditorData = data;
    groupLayout = data.layout;
    renderGroupsEditor();
  } catch (e) {
    $("groups-editor").innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
  }
}

$("add-group-btn")?.addEventListener("click", async () => {
  const name = $("new-group-name")?.value?.trim();
  if (!name) return showGroupsBanner("groups-error", "Enter a group name.");
  try {
    const res = await api(`${API_PREFIX}/groups`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name })
    });
    groupsEditorData.layout = res.layout;
    groupLayout = res.layout;
    $("new-group-name").value = "";
    renderGroupsEditor();
    showGroupsBanner("groups-success", `Created group "${name}".`);
  } catch (e) {
    showGroupsBanner("groups-error", e.message);
  }
});

$("save-groups-btn")?.addEventListener("click", async () => {
  if (!groupsEditorData?.layout) return;
  const btn = $("save-groups-btn");
  btn.disabled = true;
  try {
    const res = await api(`${API_PREFIX}/groups/layout`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layout: groupsEditorData.layout })
    });
    groupLayout = res.layout;
    groupsEditorData.layout = res.layout;
    containerListKey = "";
    await refreshContainers();
    showGroupsBanner("groups-success", "Layout saved to your account.");
  } catch (e) {
    showGroupsBanner("groups-error", e.message);
  } finally {
    btn.disabled = false;
  }
});

async function loadTierFeaturesEditor() {
  const editor = $("tier-features-editor");
  if (!editor) return;
  try {
    tierFeaturesData = await api(`${API_PREFIX}/admin/tier-features`);
    const { tiers, features, feature_labels, tier_features } = tierFeaturesData;
    editor.innerHTML = `
      <div class="tier-features-grid">
        <div class="tier-features-head">
          <span>Feature</span>
          ${tiers.map(t => `<span class="tier-col">${t}</span>`).join("")}
        </div>
        ${features.map(fid => `
          <div class="tier-features-row">
            <span class="tier-feature-name">${escapeHtml(feature_labels[fid] || fid)}</span>
            ${tiers.map(t => `
              <label class="tier-toggle">
                <input type="checkbox" data-tier="${t}" data-feature="${fid}"
                  ${tier_features[t]?.[fid] ? "checked" : ""} />
              </label>
            `).join("")}
          </div>
        `).join("")}
      </div>
    `;
  } catch (e) {
    editor.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
  }
}

function showTierFeaturesBanner(id, msg) {
  const el = $(id);
  if (!el) return;
  ["tier-features-error", "tier-features-success"].forEach(x => {
    if ($(x) && x !== id) $(x).style.display = "none";
  });
  if (!msg) { el.style.display = "none"; return; }
  el.textContent = msg;
  el.style.display = "block";
}

$("save-tier-features-btn")?.addEventListener("click", async () => {
  if (!tierFeaturesData) return;
  const tiers = tierFeaturesData.tiers;
  const features = tierFeaturesData.features;
  const tier_features = {};
  tiers.forEach(t => { tier_features[t] = {}; });
  document.querySelectorAll("#tier-features-editor input[type=checkbox]").forEach(cb => {
    tier_features[cb.dataset.tier][cb.dataset.feature] = cb.checked;
  });
  const btn = $("save-tier-features-btn");
  btn.disabled = true;
  try {
    await api(`${API_PREFIX}/admin/tier-features`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tier_features })
    });
    showTierFeaturesBanner("tier-features-success", "Feature map saved.");
    await loadProfile();
  } catch (e) {
    showTierFeaturesBanner("tier-features-error", e.message);
  } finally {
    btn.disabled = false;
  }
});

function groupOrder(g) {
  const order = [
    "Core Apps", "My Databases", "My Servers", "My Web Servers",
    "Databases", "Tools & UI", "Automation", "Other"
  ];
  const i = order.indexOf(g);
  return i === -1 ? order.length : i;
}

function renderActionsHtml(c) {
  if (!c.can_manage) {
    return `<span class="readonly-tag">Core — view only</span>`;
  }
  if (c.status === "running") {
    return `<button onclick="act('${c.name}','restart')">Restart</button>
            <button class="danger" onclick="act('${c.name}','stop')">Stop</button>`;
  }
  let html = `<button class="primary" onclick="act('${c.name}','start')">Start</button>`;
  if (c.status !== "running") {
    html += `<button class="danger" onclick="deleteContainer('${c.name}')">Delete</button>`;
  }
  return html;
}

function patchContainerRows() {
  containers.forEach(c => {
    const rowMain = $(`row-main-${c.name}`);
    if (!rowMain) return;
    const cls = statusClass(c);
    const dot = rowMain.querySelector(".dot");
    if (dot) dot.className = "dot " + cls;
    const meta = rowMain.querySelector(".meta");
    if (meta) meta.textContent = c.image;
    const stat = rowMain.querySelector(".stat");
    if (stat) stat.textContent = c.status === "running" ? "up " + fmtSince(c.started_at) : "—";
    const badge = rowMain.querySelector(".badge");
    if (badge) {
      badge.className = "badge " + cls;
      badge.textContent = c.status;
    }
    const actions = rowMain.querySelector(".actions");
    if (actions) actions.innerHTML = renderActionsHtml(c);
    const openLink = rowMain.querySelector(".open-link");
    if (c.open_port) {
      const href = `http://${location.hostname}:${c.open_port}`;
      if (openLink) {
        openLink.href = href;
      } else {
        rowMain.insertAdjacentHTML(
          "beforeend",
          `<a class="open-link" href="${href}" target="_blank" rel="noopener">Open ↗</a>`
        );
      }
    } else if (openLink) {
      openLink.remove();
    }
    const details = $(`details-${c.name}`);
    if (details) {
      const portsSpan = details.querySelector(".ports span");
      const ports = c.ports.length ? c.ports.join(", ") : "no published ports";
      if (portsSpan) portsSpan.textContent = ports;
    }
    const cached = statsCache.get(c.name);
    if (cached && statsDomExists(c.name)) applyStatsToDom(c.name, cached);
  });
}

function sortContainersInGroup(items, groupId) {
  const order = groupLayout?.container_order?.[groupId] || [];
  return [...items].sort((a, b) => {
    const ai = order.indexOf(a.name);
    const bi = order.indexOf(b.name);
    if (ai === -1 && bi === -1) return a.name.localeCompare(b.name);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });
}

function render() {
  const content = $("content");
  if (containers.length === 0) {
    content.innerHTML = '<div class="empty">No containers found on this host.</div>';
    return;
  }

  const groupMap = {};
  containers.forEach(c => {
    const key = c.group_id || c.group;
    if (!groupMap[key]) groupMap[key] = { name: c.group, items: [] };
    groupMap[key].items.push(c);
  });

  const orderedKeys = [];
  if (groupLayout?.groups?.length) {
    [...groupLayout.groups]
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
      .forEach(g => orderedKeys.push(g.id));
  }
  Object.keys(groupMap).forEach(k => {
    if (!orderedKeys.includes(k)) orderedKeys.push(k);
  });

  let html = "";
  for (const key of orderedKeys) {
    const bucket = groupMap[key];
    if (!bucket) continue;
    html += `<div class="group"><div class="group-label">${escapeHtml(bucket.name)}</div>`;
    const sortedContainers = sortContainersInGroup(bucket.items, key);
    for (const c of sortedContainers) html += renderRow(c);
    html += "</div>";
  }
  content.innerHTML = html;

  containers.forEach(c => {
    const rowEl = $(`row-main-${c.name}`);
    if (rowEl) {
      rowEl.addEventListener("click", e => {
        if (e.target.closest("button") || e.target.closest("a")) return;
        toggleDetails(c.name);
      });
    }
  });
  openLogs.forEach(name => loadLogs(name));
  openRows.forEach(name => {
    const cached = statsCache.get(name);
    if (cached) {
      applyStatsToDom(name, cached);
      renderContainerMetricCharts(name);
    }
    loadContainerMetricHistory(name);
  });
}

function renderRow(c) {
  const cls = statusClass(c);
  const isOpen = openRows.has(c.name);
  const ports = c.ports.length ? c.ports.join(", ") : "no published ports";
  const stats = cachedStatTexts(c.name);
  return `
  <div class="row">
    <div class="row-main" id="row-main-${c.name}">
      <div class="dot ${cls}"></div>
      <div class="name">${c.name}</div>
      <div class="meta">${c.image}</div>
      <div class="stat">${c.status === "running" ? "up " + fmtSince(c.started_at) : "—"}</div>
      <div class="badge ${cls}">${c.status}</div>
      <div class="actions">
        ${renderActionsHtml(c)}
      </div>
      ${c.open_port != null ? `<a class="open-link" href="http://${location.hostname}:${c.open_port}" target="_blank" rel="noopener">Open ↗</a>` : ""}
    </div>
    <div class="details ${isOpen ? "open" : ""}" id="details-${c.name}">
      <div class="ports">Ports: <span>${ports}</span></div>
      <div class="ports">
        CPU: <span id="cpu-${c.name}">${stats.cpu}</span>
        &nbsp;&nbsp;|&nbsp;&nbsp;
        Memory: <span id="mem-${c.name}">${stats.mem}</span>
      </div>
      <div class="ports">
        Network: <span id="net-${c.name}">${stats.net}</span>
        &nbsp;&nbsp;|&nbsp;&nbsp;
        Disk I/O: <span id="disk-${c.name}">${stats.disk}</span>
      </div>
      <div class="metric-charts" id="metric-charts-${c.name}">
        <div class="metric-chart-card">
          <div class="metric-chart-label">CPU <span class="metric-chart-sub">live + 24h</span></div>
          <div class="history-chart-wrap metric-chart-wrap">
            <canvas class="metric-history-chart" id="metric-cpu-${c.name}"></canvas>
            <div class="chart-tooltip" id="metric-cpu-tip-${c.name}" hidden></div>
          </div>
        </div>
        <div class="metric-chart-card">
          <div class="metric-chart-label">RAM <span class="metric-chart-sub">live + 24h</span></div>
          <div class="history-chart-wrap metric-chart-wrap">
            <canvas class="metric-history-chart" id="metric-mem-${c.name}"></canvas>
            <div class="chart-tooltip" id="metric-mem-tip-${c.name}" hidden></div>
          </div>
        </div>
        <div class="metric-chart-card">
          <div class="metric-chart-label">Disk I/O <span class="metric-chart-sub">live + 24h</span></div>
          <div class="history-chart-wrap metric-chart-wrap">
            <canvas class="metric-history-chart" id="metric-disk-${c.name}"></canvas>
            <div class="chart-tooltip" id="metric-disk-tip-${c.name}" hidden></div>
          </div>
        </div>
      </div>
      <div class="logs-head">
        <span>Recent logs</span>
        <button onclick="loadLogs('${c.name}')">Refresh</button>
      </div>
      <pre class="logs" id="logs-${c.name}">${loadedLogs.has(c.name) ? "" : "Loading…"}</pre>
      ${c.can_manage ? `
      <div class="cli-block">
        <div class="logs-head">
          <span>Docker CLI</span>
          <button type="button" class="ghost-btn small-btn" onclick="openTerminal('${c.name}')">Open terminal</button>
        </div>
      </div>` : ""}
    </div>
  </div>`;
}

function ensureTerminalHistory(name) {
  if (!terminalHistory.has(name)) {
    terminalHistory.set(name, [
      { type: "sys", text: `Connected to ${name}. Type commands below (cd persists per session).` }
    ]);
  }
  if (!terminalCwd.has(name)) terminalCwd.set(name, "/");
  return terminalHistory.get(name);
}

function updateTerminalPrompt() {
  const el = $("terminal-prompt");
  if (!el || !terminalContainer) return;
  const shell = terminalShell.get(terminalContainer);
  if (shell) {
    el.textContent = shell;
    return;
  }
  const cwd = terminalCwd.get(terminalContainer) || "/";
  el.textContent = `${cwd} $`;
}

function renderTerminalScreen() {
  const screen = $("terminal-screen");
  if (!screen || !terminalContainer) return;
  const lines = ensureTerminalHistory(terminalContainer);
  screen.innerHTML = lines.map(line => {
    const cls = line.type === "in" ? "term-in"
      : line.type === "err" ? "term-err"
      : line.type === "sys" ? "term-sys"
      : "term-out";
    return `<div class="${cls}">${escapeHtml(line.text)}</div>`;
  }).join("");
  screen.scrollTop = screen.scrollHeight;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

window.openTerminal = function openTerminal(name) {
  terminalContainer = name;
  ensureTerminalHistory(name);
  $("terminal-title").textContent = `Docker CLI — ${name}`;
  $("terminal-modal").hidden = false;
  renderTerminalScreen();
  updateTerminalPrompt();
  $("terminal-input").value = "";
  $("terminal-input").focus();
};

function closeTerminal() {
  $("terminal-modal").hidden = true;
  if (terminalContainer) terminalShell.delete(terminalContainer);
  terminalContainer = null;
}

$("terminal-close")?.addEventListener("click", closeTerminal);
$("terminal-backdrop")?.addEventListener("click", closeTerminal);

$("terminal-form")?.addEventListener("submit", async e => {
  e.preventDefault();
  const input = $("terminal-input");
  const cmd = input?.value?.trim();
  if (!cmd || !terminalContainer) return;

  const history = ensureTerminalHistory(terminalContainer);
  const prompt = terminalShell.get(terminalContainer) || `${terminalCwd.get(terminalContainer) || "/"} $`;
  history.push({ type: "in", text: `${prompt} ${cmd}` });
  input.value = "";
  renderTerminalScreen();

  try {
    const res = await api(`${API_PREFIX}/containers/${terminalContainer}/exec`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command: cmd,
        cwd: terminalCwd.get(terminalContainer) || "/"
      })
    });
    if (res.cwd) terminalCwd.set(terminalContainer, res.cwd);
    if (res.shell) {
      terminalShell.set(terminalContainer, res.prompt || `${res.shell}>`);
    } else {
      terminalShell.delete(terminalContainer);
    }
    updateTerminalPrompt();
    const out = res.output || "(no output)";
    if (res.exit_code !== 0) {
      history.push({ type: "err", text: `[exit ${res.exit_code}]\n${out}` });
    } else {
      history.push({ type: "out", text: out });
    }
  } catch (err) {
    history.push({ type: "err", text: `Error: ${err.message}` });
  }
  renderTerminalScreen();
  input.focus();
});

document.addEventListener("keydown", e => {
  if (e.key === "Escape" && !$("confirm-modal")?.hidden) {
    closeConfirm(false);
    return;
  }
  if (e.key === "Escape" && !$("terminal-modal")?.hidden) closeTerminal();
});

window.toggleDetails = async function toggleDetails(name) {
  const el = $(`details-${name}`);
  if (openRows.has(name)) {
    openRows.delete(name);
    openLogs.delete(name);
    el.classList.remove("open");
  } else {
    openRows.add(name);
    openLogs.add(name);
    el.classList.add("open");
    const cached = statsCache.get(name);
    if (cached) {
      applyStatsToDom(name, cached);
      pushContainerLivePoint(name, cached);
      renderContainerMetricCharts(name);
    }
    loadStats(name, { force: true });
    loadContainerMetricHistory(name);
    await Promise.all([
      loadedLogs.has(name) ? Promise.resolve() : loadLogs(name).then(() => loadedLogs.add(name))
    ]);
  }
};

window.loadLogs = async function loadLogs(name) {
  const el = $(`logs-${name}`);
  if (!el) return;
  try {
    const data = await api(`${API_PREFIX}/containers/${name}/logs?tail=200`);
    el.textContent = data.logs || "(no log output)";
    el.scrollTop = el.scrollHeight;
  } catch (e) {
    el.textContent = "Could not load logs: " + e.message;
  }
};

async function loadStats(name, { force = false } = {}) {
  if (!force) {
    if (statsInFlight.has(name)) return;
    if (!shouldRefreshStats(name)) return;
  }

  statsInFlight.add(name);
  try {
    const s = await api(`${API_PREFIX}/containers/${name}/stats`);
    statsCache.set(name, s);
    statsLastFetched.set(name, Date.now());
    if (statsDomExists(name)) applyStatsToDom(name, s);
    pushContainerLivePoint(name, s);
    if (openRows.has(name)) renderContainerMetricCharts(name);
  } catch (e) {
    console.error("stats", name, e);
  } finally {
    statsInFlight.delete(name);
  }
}

function refreshOpenRowStats() {
  if (openRows.size === 0) return;
  for (const name of openRows) {
    const c = containers.find(x => x.name === name);
    if (!c || c.status !== "running") continue;
    loadStats(name, { force: true });
  }
}

function refreshContainerStats() {
  for (const c of containers) {
    if (c.status !== "running") continue;
    if (openRows.has(c.name)) continue;
    if (shouldRefreshStats(c.name)) loadStats(c.name);
  }
}

window.act = async function act(name, action) {
  try {
    await api(`${API_PREFIX}/containers/${name}/${action}`, { method: "POST" });
    statsCache.delete(name);
    statsLastFetched.delete(name);
    containerListKey = "";
    await refreshContainers();
  } catch (e) {
    showError(`Failed to ${action} ${name}: ` + e.message);
  }
};

window.deleteContainer = async function deleteContainer(name) {
  if (!await showConfirm(`Delete ${name}? This cannot be undone.`, {
    title: "Delete container",
    confirmLabel: "Delete"
  })) return;
  try {
    await api(`${API_PREFIX}/containers/${name}`, { method: "DELETE" });
    openRows.delete(name);
    openLogs.delete(name);
    loadedLogs.delete(name);
    statsCache.delete(name);
    statsLastFetched.delete(name);
    containerListKey = "";
    await refreshContainers();
  } catch (e) {
    showError(`Failed to delete ${name}: ` + e.message);
  }
};

/* ---------------- Create DB form ---------------- */

document.querySelectorAll(".engine-opt").forEach(opt => {
  opt.addEventListener("click", () => {
    document.querySelectorAll(".engine-opt").forEach(o => o.classList.remove("selected"));
    opt.classList.add("selected");
    selectedEngine = opt.dataset.engine;
    applyEngineFieldVisibility();
  });
});

function applyEngineFieldVisibility() {
  const isRedis = selectedEngine === "redis";
  $("row-username").style.display = isRedis ? "none" : "flex";
  $("row-dbname").style.display = isRedis ? "none" : "flex";
  const tablesApplicable =
    selectedEngine === "postgres" || selectedEngine === "mysql" || selectedEngine === "mongo";
  $("tables-label").textContent =
    selectedEngine === "mongo" ? "Initial collections (optional)" : "Initial tables (optional)";
  $("tables-label").style.display = tablesApplicable ? "block" : "none";
  $("tables-container").style.display = tablesApplicable ? "block" : "none";
  $("add-table-btn").style.display = tablesApplicable ? "block" : "none";
}

window.addTableBlock = function addTableBlock() {
  tableCount++;
  const id = "tbl_" + tableCount;
  const wrap = document.createElement("div");
  wrap.className = "table-block";
  wrap.id = id;
  const isMongo = selectedEngine === "mongo";
  wrap.innerHTML = `
    <div class="table-block-head">
      <input type="text" placeholder="${isMongo ? "collection name" : "table name"}" class="tbl-name" />
      <button class="small-btn danger" onclick="document.getElementById('${id}').remove()">✕</button>
    </div>
    <div class="cols-holder" style="display:${isMongo ? "none" : "block"}"></div>
    <button class="ghost-btn small-btn" type="button" onclick="addColumnRow('${id}')" style="display:${isMongo ? "none" : "block"}">+ Add column</button>
  `;
  $("tables-container").appendChild(wrap);
  if (!isMongo) addColumnRow(id);
};

window.addColumnRow = function addColumnRow(tableId) {
  const holder = document.querySelector(`#${tableId} .cols-holder`);
  const row = document.createElement("div");
  row.className = "col-row";
  const typeOptions = COL_TYPES.map(t => `<option value="${t}">${t}</option>`).join("");
  row.innerHTML = `
    <input type="text" placeholder="column name" class="col-name" />
    <select class="col-type">${typeOptions}</select>
    <button class="small-btn danger" type="button" onclick="this.parentElement.remove()">✕</button>
  `;
  holder.appendChild(row);
};

$("add-table-btn").addEventListener("click", addTableBlock);

function collectTables() {
  const tables = [];
  document.querySelectorAll(".table-block").forEach(block => {
    const tname = block.querySelector(".tbl-name").value.trim();
    if (!tname) return;
    const columns = [];
    block.querySelectorAll(".col-row").forEach(row => {
      const cname = row.querySelector(".col-name").value.trim();
      const ctype = row.querySelector(".col-type").value;
      if (cname) columns.push({ name: cname, type: ctype });
    });
    tables.push({ name: tname, columns });
  });
  return tables;
}

function showCreateError(msg) {
  const el = $("create-error");
  if (!msg) {
    el.style.display = "none";
    return;
  }
  el.textContent = msg;
  el.style.display = "block";
}

function showCreateSuccess(msg) {
  const el = $("create-success");
  if (!msg) {
    el.style.display = "none";
    return;
  }
  el.textContent = msg;
  el.style.display = "block";
}

function parseHostPort(raw) {
  if (raw === "" || raw == null) return null;
  const port = Number.parseInt(String(raw).trim(), 10);
  if (!Number.isFinite(port)) return null;
  return port;
}

function validateHostPort(raw) {
  const port = parseHostPort(raw);
  if (port == null) return "Pick a host port.";
  if (port < 1) return "Port cannot be zero or negative.";
  if (port < 1024) return "Port must be at least 1024.";
  if (port > 65535) return "Port cannot exceed 65535.";
  if (isPortUsedByContainer(port)) {
    return `Port ${port} is already used by another container on this host.`;
  }
  return null;
}

function isPortUsedByContainer(port) {
  const needle = `${port}->`;
  return containers.some(c => (c.ports || []).some(binding => binding.startsWith(needle)));
}

function updatePortHint() {
  const hint = $("port-hint");
  const input = $("f-port");
  if (!hint || !input) return;

  const err = validateHostPort(input.value);
  if (err && input.value !== "") {
    hint.textContent = err;
    hint.style.color = "var(--red)";
  } else {
    hint.textContent = "Must be between 1024 and 65535.";
    hint.style.color = "var(--muted)";
  }
}

function showBanner(elId, msg) {
  const el = $(elId);
  if (!el) return;
  if (!msg) {
    el.style.display = "none";
    return;
  }
  el.textContent = msg;
  el.style.display = "block";
}

$("f-port")?.addEventListener("input", () => {
  const input = $("f-port");
  if (!input) return;
  if (input.value !== "" && Number(input.value) < 0) {
    input.value = String(Math.abs(Number(input.value)));
  }
  updatePortHint();
});

document.querySelectorAll("#web-type-grid .engine-opt").forEach(opt => {
  opt.addEventListener("click", () => {
    document.querySelectorAll("#web-type-grid .engine-opt").forEach(o => o.classList.remove("selected"));
    opt.classList.add("selected");
    selectedWebType = opt.dataset.type;
  });
});

function collectLanguages() {
  return [...document.querySelectorAll("#lang-grid input:checked")].map(cb => cb.value);
}

$("submit-ubuntu-btn")?.addEventListener("click", async () => {
  showBanner("ubuntu-error", null);
  showBanner("ubuntu-success", null);
  $("ubuntu-result").innerHTML = "";

  const name = $("ubuntu-name").value.trim();
  if (!name) return showBanner("ubuntu-error", "Enter a server name.");

  const btn = $("submit-ubuntu-btn");
  btn.disabled = true;
  btn.textContent = "Creating…";

  try {
    const res = await api(`${API_PREFIX}/servers/ubuntu`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        persistent: $("ubuntu-persistent").checked,
        languages: collectLanguages()
      })
    });
    const langs = res.languages?.length ? res.languages.join(", ") : "none";
    showBanner(
      "ubuntu-success",
      `Created ${res.container.name}. Languages: ${langs}. Workspace: ${res.workspace}`
    );
    $("ubuntu-result").innerHTML =
      `<div class="ok">Expand the container on the dashboard and use <b>Open terminal</b> to run commands.</div>`;
    containerListKey = "";
    refreshContainers();
  } catch (e) {
    showBanner("ubuntu-error", e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Create Ubuntu server";
  }
});

$("submit-web-btn")?.addEventListener("click", async () => {
  showBanner("web-error", null);
  showBanner("web-success", null);
  $("web-result").innerHTML = "";

  const name = $("web-name").value.trim();
  if (!name) return showBanner("web-error", "Enter a server name.");

  const rawPort = $("web-port").value.trim();
  let hostPort = null;
  if (rawPort !== "") {
    hostPort = parseHostPort(rawPort);
    if (hostPort == null || hostPort < 1024 || hostPort > 65535) {
      return showBanner("web-error", "Host port must be between 1024 and 65535.");
    }
    if (isPortUsedByContainer(hostPort)) {
      return showBanner("web-error", `Port ${hostPort} is already used by another container.`);
    }
  }

  const btn = $("submit-web-btn");
  btn.disabled = true;
  btn.textContent = "Creating…";

  try {
    const payload = {
      name,
      type: selectedWebType,
      persistent: $("web-persistent").checked
    };
    if (hostPort != null) payload.host_port = hostPort;

    const res = await api(`${API_PREFIX}/servers/web`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    showBanner("web-success", `Created ${res.container.name} (${res.type}) at ${res.url}`);
    $("web-result").innerHTML =
      `<div class="ok"><a href="${res.url}" target="_blank" rel="noopener">Open ${res.url}</a></div>`;
    containerListKey = "";
    refreshContainers();
  } catch (e) {
    showBanner("web-error", e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Create web server";
  }
});

$("submit-db-btn").addEventListener("click", async () => {
  showCreateError(null);
  showCreateSuccess(null);
  $("result-block").innerHTML = "";

  const portError = validateHostPort($("f-port").value);
  if (portError) return showCreateError(portError);

  const payload = {
    engine: selectedEngine,
    name: $("f-name").value.trim(),
    host_port: parseHostPort($("f-port").value),
    username: $("f-username").value.trim(),
    password: $("f-password").value,
    db_name: $("f-dbname").value.trim(),
    tables: collectTables(),
    persistent: $("f-persistent").checked
  };

  if (!payload.name) return showCreateError("Give it an identifier first.");

  const btn = $("submit-db-btn");
  btn.disabled = true;
  btn.textContent = "Creating…";

  try {
    const res = await api(`${API_PREFIX}/databases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    showCreateSuccess(
      `Created zeno_userdb_${payload.name} (${selectedEngine}) on port ${payload.host_port}. ` +
        (res.ready ? "It is up and responding." : res.warning || "")
    );
    if (res.tables && res.tables.length) {
      $("result-block").innerHTML = res.tables.map(t =>
        `<div class="${t.ok ? "ok" : "bad"}">${t.ok ? "✓" : "✗"} ${t.table}${t.ok ? "" : " — " + t.detail}</div>`
      ).join("");
    }
    containerListKey = "";
    refreshContainers();
  } catch (e) {
    showCreateError(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Create database";
  }
});

function scheduleResizeRedraw() {
  if (resizeRaf) cancelAnimationFrame(resizeRaf);
  resizeRaf = requestAnimationFrame(() => {
    renderSparklines();
    openRows.forEach(name => renderContainerMetricCharts(name));
  });
}

/* ---------------- Timeline ---------------- */

function formatTs(ts) {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

async function loadTimeline() {
  const list = $("timeline-list");
  const err = $("timeline-error");
  if (!list) return;
  try {
    const data = await api(`${API_PREFIX}/timeline?hours=24&limit=200`);
    const events = (data.events || []).filter(evt => {
      if (timelineFilter === "all") return true;
      if (timelineFilter === "alert") return evt.type === "alert";
      if (timelineFilter === "operation") return evt.type === "operation";
      if (timelineFilter === "state") return evt.type === "state";
      return true;
    });
    if (!events.length) {
      list.innerHTML = '<div class="empty">No events in the last 24 hours.</div>';
      return;
    }
    list.innerHTML = events.map(evt => {
      const sev = evt.severity || "info";
      const meta = [
        evt.type,
        evt.username ? `by ${evt.username}` : "",
        evt.container ? `container: ${evt.container}` : "",
        evt.resolved === false ? "active" : "",
        evt.resolved === true ? "resolved" : ""
      ].filter(Boolean).join(" · ");
      return `
        <div class="timeline-item severity-${sev}" data-container="${escapeHtml(evt.container || "")}">
          <div class="timeline-item-head">
            <div class="timeline-item-title">${escapeHtml(evt.title || "Event")}</div>
            <div class="timeline-item-ts">${escapeHtml(formatTs(evt.ts))}</div>
          </div>
          ${evt.detail ? `<div class="timeline-item-detail">${escapeHtml(evt.detail)}</div>` : ""}
          <div class="timeline-item-meta">${escapeHtml(meta)}</div>
        </div>`;
    }).join("");
    list.querySelectorAll(".timeline-item[data-container]").forEach(item => {
      const name = item.dataset.container;
      if (!name) return;
      item.style.cursor = "pointer";
      item.addEventListener("click", () => {
        document.querySelector('[data-view="dashboard"]')?.click();
        setTimeout(() => {
          if (!openRows.has(name)) toggleDetails(name);
          const row = $(`row-main-${name}`);
          row?.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 100);
      });
    });
    if (err) err.style.display = "none";
  } catch (e) {
    if (err) {
      err.textContent = e.message;
      err.style.display = "block";
    }
    list.innerHTML = '<div class="empty">Could not load timeline.</div>';
  }
}

document.querySelectorAll("#timeline-filters .filter-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#timeline-filters .filter-chip").forEach(c =>
      c.classList.remove("active")
    );
    chip.classList.add("active");
    timelineFilter = chip.dataset.filter || "all";
    loadTimeline();
  });
});

$("timeline-refresh")?.addEventListener("click", loadTimeline);

/* ---------------- Central logs page ---------------- */

function drawHistoryChart(canvas, points, color, getValue, hoverIdx = -1, yMax = null) {
  if (!canvas || !points.length) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (w === 0 || h === 0) return;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const pad = 8;
  const maxVal = yMax != null
    ? yMax
    : Math.max(100, ...points.map(getValue), 1);
  const data = points.map(getValue);
  const stepX = points.length > 1 ? (w - pad * 2) / (points.length - 1) : 0;
  const coords = data.map((v, i) => {
    const x = pad + i * stepX;
    const y = h - pad - (Math.min(v, maxVal) / maxVal) * (h - pad * 2);
    return [x, y, v];
  });

  if (coords.length >= 2) {
    ctx.beginPath();
    ctx.moveTo(coords[0][0], h - pad);
    coords.forEach(([x, y]) => ctx.lineTo(x, y));
    ctx.lineTo(coords[coords.length - 1][0], h - pad);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, pad, 0, h - pad);
    grad.addColorStop(0, hexToRgba(color, 0.25));
    grad.addColorStop(1, hexToRgba(color, 0));
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    coords.forEach(([x, y], i) => {
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  if (hoverIdx >= 0 && coords[hoverIdx]) {
    const [hx, hy] = coords[hoverIdx];
    ctx.beginPath();
    ctx.arc(hx, hy, 4, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1;
    ctx.stroke();
  }
}

function wireHistoryChart(canvasId, tooltipId, points, color, getValue, label, yMax = null, valueSuffix = "%") {
  const canvas = $(canvasId);
  const tooltip = $(tooltipId);
  const wrap = canvas?.parentElement;
  if (!canvas) return;

  const redraw = (hoverIdx = -1) => {
    drawHistoryChart(canvas, points, color, getValue, hoverIdx, yMax);
  };
  redraw();

  canvas.onmousemove = e => {
    if (!points.length) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const idx = Math.max(0, Math.min(
      points.length - 1,
      Math.round((x / Math.max(rect.width, 1)) * (points.length - 1))
    ));
    redraw(idx);
    if (tooltip && wrap && points[idx]) {
      const val = getValue(points[idx]);
      tooltip.hidden = false;
      tooltip.textContent = `${label}: ${val.toFixed(1)}${valueSuffix} · ${formatTs(points[idx].ts)}`;
      const wrapRect = wrap.getBoundingClientRect();
      tooltip.style.left = `${e.clientX - wrapRect.left + 8}px`;
      tooltip.style.top = `${e.clientY - wrapRect.top - 28}px`;
    }
  };
  canvas.onmouseleave = () => {
    redraw();
    if (tooltip) tooltip.hidden = true;
  };
}

async function loadMyActivity() {
  const el = $("user-activity-log");
  if (!el) return;
  try {
    const data = await api(`${API_PREFIX}/activity/me?limit=200`);
    const entries = data.entries || [];
    if (!entries.length) {
      el.innerHTML = '<div class="empty">No activity recorded for your account yet.</div>';
      return;
    }
    el.innerHTML = entries.map(entry => {
      const action = entry.action || "event";
      const container = entry.container ? ` · ${entry.container}` : "";
      const detail = entry.details || entry.container_image || "";
      return `
        <div class="activity-log-entry action-${escapeHtml(action)}">
          <div class="act-ts">${escapeHtml(formatTs(entry.ts))}</div>
          <div class="act-action">${escapeHtml(action)}${escapeHtml(container)}</div>
          ${detail ? `<div class="act-detail">${escapeHtml(detail)}</div>` : ""}
        </div>`;
    }).join("");
  } catch (e) {
    el.innerHTML = `<div class="empty">Could not load activity: ${escapeHtml(e.message)}</div>`;
  }
}

async function loadCentralLogsPage() {
  await loadGroupLayout();
  loadMyActivity();
  populateLogsDropdowns();
  refreshCentralLogs();
}

/* ---------------- Container log search ---------------- */

function containersForLogsGroup(groupId) {
  if (!containers.length) return [];
  if (!groupId) {
    return [...containers].sort((a, b) => a.name.localeCompare(b.name));
  }
  if (groupLayout?.assignments) {
    return containersInGroup(groupLayout, containers, groupId);
  }
  return containers
    .filter(c => (c.group_id || c.group) === groupId)
    .sort((a, b) => a.name.localeCompare(b.name));
}

function populateLogsDropdowns() {
  const groupSel = $("logs-group");
  const containerSel = $("logs-container");
  if (!groupSel || !containerSel) return;

  const prevGroup = groupSel.value;
  const prevContainer = containerSel.value;

  const groups = groupLayout?.groups?.length
    ? [...groupLayout.groups].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
    : [];

  groupSel.innerHTML = '<option value="">All groups</option>' +
    groups.map(g =>
      `<option value="${escapeHtml(g.id)}">${escapeHtml(g.name)}</option>`
    ).join("");

  if (prevGroup && groups.some(g => g.id === prevGroup)) {
    groupSel.value = prevGroup;
  }

  const list = containersForLogsGroup(groupSel.value);
  containerSel.innerHTML = '<option value="">Select a container…</option>' +
    list.map(c =>
      `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`
    ).join("");

  if (prevContainer && list.some(c => c.name === prevContainer)) {
    containerSel.value = prevContainer;
  } else if (prevContainer && !groupSel.value) {
    const stillExists = containers.some(c => c.name === prevContainer);
    if (stillExists) containerSel.value = prevContainer;
  }
}

$("logs-group")?.addEventListener("change", () => {
  const containerSel = $("logs-container");
  if (containerSel) containerSel.value = "";
  populateLogsDropdowns();
});

async function refreshCentralLogs() {
  const out = $("central-logs-output");
  const err = $("logs-view-error");
  if (!out) return;
  const name = $("logs-container")?.value || "";
  if (!name) {
    out.textContent = "Select a container.";
    return;
  }
  const search = ($("logs-search")?.value || "").trim();
  const tail = $("logs-tail")?.value || "300";
  try {
    const qs = new URLSearchParams({
      containers: name,
      tail,
      ...(search ? { search } : {})
    });
    const data = await api(`${API_PREFIX}/logs/central?${qs}`);
    out.textContent = (data.lines || [])
      .map(row => `[${row.container}] ${row.line}`)
      .join("\n") || "(no matching log lines)";
    if (err) err.style.display = "none";
  } catch (e) {
    if (err) {
      err.textContent = e.message;
      err.style.display = "block";
    }
    out.textContent = "Failed to load logs.";
  }
}

$("logs-refresh-btn")?.addEventListener("click", refreshCentralLogs);
$("logs-search")?.addEventListener("keydown", e => {
  if (e.key === "Enter") refreshCentralLogs();
});
$("logs-auto-refresh")?.addEventListener("change", e => {
  if (logsAutoTimer) {
    clearInterval(logsAutoTimer);
    logsAutoTimer = null;
  }
  if (e.target.checked) {
    logsAutoTimer = setInterval(refreshCentralLogs, 10000);
  }
});

/* ---------------- Alerts page ---------------- */

const ALERT_RULE_LABELS = {
  cpu_high: "CPU high",
  mem_high: "Memory high",
  crash_loop: "Crash loop",
  port_failure: "Port failure"
};

async function loadAlertsPage() {
  const err = $("alerts-view-error");
  try {
    const data = await api(`${API_PREFIX}/alerts?hours=24&containers_only=true`);
    const thresholds = data.thresholds || { cpu_percent: 90, mem_percent: 90 };
    const cpuInput = $("alert-cpu-threshold");
    const memInput = $("alert-mem-threshold");
    if (cpuInput) cpuInput.value = thresholds.cpu_percent;
    if (memInput) memInput.value = thresholds.mem_percent;
    renderAlertsList(data.alerts || []);
    renderAlertsUI((data.alerts || []).filter(a => !a.resolved));
    if (err) err.style.display = "none";
  } catch (e) {
    if (err) {
      err.textContent = e.message;
      err.style.display = "block";
    }
    const list = $("alerts-list");
    if (list) list.innerHTML = '<div class="empty">Could not load alerts.</div>';
  }
}

function renderAlertsList(alerts) {
  const list = $("alerts-list");
  if (!list) return;
  const filtered = alerts.filter(a => {
    if (alertFilter === "all") return true;
    return a.rule === alertFilter;
  });
  if (!filtered.length) {
    list.innerHTML = '<div class="empty">No container alerts in the last 24 hours.</div>';
    return;
  }
  list.innerHTML = filtered.map(alert => {
    const ruleLabel = ALERT_RULE_LABELS[alert.rule] || alert.rule;
    const metrics = [];
    if (alert.cpu_percent != null) metrics.push(`CPU ${alert.cpu_percent}%`);
    if (alert.mem_percent != null) metrics.push(`Memory ${alert.mem_percent}%`);
    const status = alert.resolved ? "resolved" : "active";
    return `
      <div class="timeline-item severity-${alert.severity || "warning"}" data-container="${escapeHtml(alert.container || "")}">
        <div class="timeline-item-head">
          <div class="timeline-item-title">${escapeHtml(alert.message || ruleLabel)}</div>
          <div class="timeline-item-ts">${escapeHtml(formatTs(alert.ts))}</div>
        </div>
        <div class="timeline-item-detail">${escapeHtml(ruleLabel)} · ${escapeHtml(alert.container || "")} · ${status}</div>
        ${metrics.length ? `<div class="alert-metrics">${escapeHtml(metrics.join(" · "))}</div>` : ""}
      </div>`;
  }).join("");
  list.querySelectorAll(".timeline-item[data-container]").forEach(item => {
    const name = item.dataset.container;
    if (!name) return;
    item.style.cursor = "pointer";
    item.addEventListener("click", () => {
      document.querySelector('[data-view="dashboard"]')?.click();
      setTimeout(() => {
        if (!openRows.has(name)) toggleDetails(name);
        $(`row-main-${name}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 100);
    });
  });
}

document.querySelectorAll("#alerts-filters .filter-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#alerts-filters .filter-chip").forEach(c =>
      c.classList.remove("active")
    );
    chip.classList.add("active");
    alertFilter = chip.dataset.alertFilter || "all";
    loadAlertsPage();
  });
});

$("alerts-refresh-btn")?.addEventListener("click", loadAlertsPage);

function clampThresholdInput(input) {
  if (!input) return;
  let val = parseInt(input.value, 10);
  if (Number.isNaN(val)) val = 90;
  val = Math.max(1, Math.min(100, val));
  input.value = String(val);
}

document.querySelectorAll(".stepper-btn[data-stepper]").forEach(btn => {
  btn.addEventListener("click", () => {
    const input = $(btn.dataset.stepper);
    if (!input) return;
    const delta = Number(btn.dataset.delta) || 0;
    const next = Math.max(1, Math.min(100, (parseInt(input.value, 10) || 0) + delta));
    input.value = String(next);
  });
});

["alert-cpu-threshold", "alert-mem-threshold"].forEach(id => {
  $(id)?.addEventListener("change", e => clampThresholdInput(e.target));
});

$("alert-threshold-save")?.addEventListener("click", async () => {
  const msg = $("alert-threshold-msg");
  const cpu = Number($("alert-cpu-threshold")?.value);
  const mem = Number($("alert-mem-threshold")?.value);
  try {
    await api(`${API_PREFIX}/alerts/thresholds`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cpu_percent: cpu, mem_percent: mem })
    });
    if (msg) {
      msg.textContent = "Thresholds saved.";
      msg.hidden = false;
      msg.className = "threshold-save-msg ok";
    }
    loadAlertsPage();
  } catch (e) {
    if (msg) {
      msg.textContent = e.message;
      msg.hidden = false;
      msg.className = "threshold-save-msg bad";
    }
  }
});

/* ---------------- Alerts badge / banner ---------------- */

function renderAlertsUI(alerts) {
  const active = alertsVisibleOnDashboard(alerts).filter(a => !a.resolved);
  const badge = $("alerts-badge");
  const countEl = $("alerts-badge-count");
  const banner = $("alerts-banner");

  if (badge && countEl) {
    badge.hidden = false;
    countEl.textContent = String(active.length);
    badge.classList.toggle("alerts-badge-active", active.length > 0);
  }

  if (banner) {
    const critical = active.filter(a => a.severity === "critical");
    if (!critical.length) {
      banner.hidden = true;
      banner.innerHTML = "";
    } else {
      banner.hidden = false;
      banner.innerHTML = critical.map(a =>
        `<div class="alerts-banner-item">${escapeHtml(a.message)}</div>`
      ).join("");
    }
  }
}

async function refreshAlerts() {
  try {
    const data = await api(`${API_PREFIX}/alerts?hours=24&active_only=true&containers_only=true`);
    renderAlertsUI(data.alerts || []);
  } catch (e) {
    console.error("alerts", e);
  }
}

$("alerts-badge")?.addEventListener("click", () => {
  document.querySelector('[data-view="alerts"]')?.click();
});

/* ---------------- Boot ---------------- */

async function boot() {
  if (location.pathname === "/login") return;

  applyEngineFieldVisibility();
  await loadProfile();
  startSparkAnimation();
  refreshContainers();
  refreshHostStats();
  hostStatsTimer = setInterval(refreshHostStats, HOST_POLL_MS);
  containerRefreshTimer = setInterval(refreshContainers, 10000);
  statsRefreshTimer = setInterval(refreshContainerStats, STATS_TICK_MS);
  openStatsTimer = setInterval(refreshOpenRowStats, STATS_POLL_OPEN_MS);
  alertsTimer = setInterval(refreshAlerts, 30000);
  refreshAlerts();
  window.addEventListener("resize", scheduleResizeRedraw);
}

document.addEventListener("DOMContentLoaded", boot);
