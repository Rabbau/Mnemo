"use strict";

// ================================================================ utils
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Number(n || 0).toLocaleString("ru-RU");
const fmtDate = (ts) => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" });
const pad = (n) => String(n).padStart(2, "0");
const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? few : many;
};

const PALETTE = ["#8b6cf6", "#38bdf8", "#f472b6", "#4ade80", "#fbbf24", "#fb923c", "#2dd4bf", "#e879f9", "#a3e635", "#f87171", "#60a5fa", "#c084fc"];

const S = {
  config: null, stats: null, graph: null, fg: null, folders: [],
  note: null, notesList: [], chat: [], chatAbort: null, ollama: null,
  heatMode: "modified", charts: {}, loaded: {}, colorMap: {},
};

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.body && typeof o.body !== "string") {
    o.body = JSON.stringify(o.body);
    o.headers["Content-Type"] = "application/json";
    o.method = o.method || "POST";
  }
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) throw new Error((data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || `Ошибка ${r.status}`);
  if (data?.manual_required && opts.body && typeof opts.body === "object") {
    // web-chat mode: ask the user for the model's answer and replay the request with it
    const answer = await manualDialog(data.manual_required);
    return api(path, { ...opts, body: withAnswer(opts.body, answer) });
  }
  return data;
}

const withAnswer = (body, answer) => ({ ...body, manual_answers: [...(body.manual_answers || []), answer] });

// Reads an NDJSON stream of events: {type: "token"|"sources"|"model"|"manual"|"done"|"error", ...}
async function stream(path, body, onEvent, signal) {
  let manual = null;
  await readStream(path, body, (ev) => { if (ev.type === "manual") manual = ev; else onEvent(ev); }, signal);
  if (manual) {
    const answer = await manualDialog(manual);
    return stream(path, withAnswer(body, answer), onEvent, signal);
  }
}

async function readStream(path, body, onEvent, signal) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  if (!r.ok) {
    let d = null; try { d = await r.json(); } catch { /* */ }
    throw new Error(d?.detail || `Ошибка ${r.status}`);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  const handle = (line) => {
    if (!line.trim()) return;
    const ev = JSON.parse(line);
    if (ev.type === "error") throw new Error(ev.message);
    onEvent(ev);
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) { handle(buf.slice(0, i)); buf = buf.slice(i + 1); }
  }
  handle(buf);
}

// ---------- web-chat mode: "copy the prompt, paste the answer"
function manualDialog(info) {
  return new Promise((resolve, reject) => {
    const box = $("#manual");
    $("#manual-step").textContent = `Шаг ${info.step}`;
    $("#manual-title").textContent = info.title;
    $("#manual-prompt").value = info.prompt;
    $("#manual-size").textContent = `· ${fmt(info.prompt.length)} символов`;
    $("#manual-expects").textContent = info.expects === "json" ? "· в ответе должен быть JSON" : "";
    $("#manual-answer").value = "";
    $("#manual-error").textContent = "";
    $("#manual-skip").classList.toggle("hidden", !info.skippable);
    box.classList.remove("hidden");
    const finish = (fn, value) => {
      box.classList.add("hidden");
      for (const id of ["#manual-ok", "#manual-cancel", "#manual-skip", "#manual-copy"]) $(id).onclick = null;
      fn(value);
    };
    $("#manual-copy").onclick = async () => {
      try { await navigator.clipboard.writeText(info.prompt); }
      catch { $("#manual-prompt").select(); document.execCommand("copy"); }
      toast("Запрос скопирован — вставь его в чат", "ok");
      $("#manual-answer").focus();
    };
    $("#manual-ok").onclick = () => {
      const a = $("#manual-answer").value.trim();
      if (!a) return ($("#manual-error").textContent = "Вставь ответ чата");
      if (info.expects === "json" && !/\{[\s\S]*\}/.test(a)) {
        return ($("#manual-error").textContent = "В ответе не видно JSON — скопируй ответ целиком кнопкой копирования");
      }
      finish(resolve, a);
    };
    $("#manual-skip").onclick = () => finish(resolve, "");
    $("#manual-cancel").onclick = () => finish(reject, new Error("Отменено"));
  });
}

function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), type === "err" ? 7000 : 3500);
}

async function busy(btn, fn) {
  const html = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> ${btn.textContent}`;
  try { return await fn(); }
  catch (e) { toast(e.message, "err"); }
  finally { btn.disabled = false; btn.innerHTML = html; }
}

function modal(title, bodyHtml, actions, cls = "") {
  $("#modal .modal-box").className = `modal-box ${cls}`;
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = bodyHtml;
  const box = $("#modal-actions");
  box.innerHTML = "";
  const close = () => $("#modal").classList.add("hidden");
  for (const a of actions) {
    const b = document.createElement("button");
    b.className = `btn ${a.cls || ""}`;
    b.textContent = a.label;
    b.onclick = async () => { if ((await a.onClick?.()) !== false) close(); };
    box.appendChild(b);
  }
  $("#modal").classList.remove("hidden");
  $("#modal-body input")?.focus();
}

const WIKILINK = /(!?)\[\[([^\[\]|#^]*)([#^][^\[\]|]*)?(?:\|([^\[\]]*))?\]\]/g;

function renderMarkdown(text, fromPath = "") {
  let src = String(text || "").replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "");
  src = src.replace(WIKILINK, (m, emb, target, anchor, alias) => {
    const t = target.trim();
    if (emb && /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(t)) {
      return `<img src="/api/attachment?target=${encodeURIComponent(t)}&src=${encodeURIComponent(fromPath)}" alt="${esc(t)}">`;
    }
    const label = alias || (t ? t + (anchor || "") : (anchor || "").slice(1));
    return `<a class="wikilink" data-target="${esc(t)}" data-from="${esc(fromPath)}">${esc(label)}</a>`;
  });
  return DOMPurify.sanitize(marked.parse(src));
}

function colorFor(top) {
  if (!(top in S.colorMap)) S.colorMap[top] = PALETTE[Object.keys(S.colorMap).length % PALETTE.length];
  return S.colorMap[top];
}

function fillFolderSelect(sel, firstLabel, includeRoot = false) {
  const cur = sel.value;
  sel.innerHTML = `<option value="">${esc(firstLabel)}</option>` + (includeRoot ? "" : "") +
    S.folders.map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join("");
  sel.value = cur;
}

// ================================================================ navigation
function showView(name) {
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  localStorage.setItem("view", name);
  if (name !== "settings" && !S.config?.vault_ok) {
    toast("Сначала подключи хранилище в настройках");
    return showView("settings");
  }
  const loaders = { dashboard: loadDashboard, graph: loadGraph, notes: loadNotes, settings: loadSettings, tools: loadTools, chat: initChat };
  loaders[name]?.();
}

$$(".nav-item").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

// wikilinks & note links anywhere in the app
document.addEventListener("click", async (e) => {
  const wl = e.target.closest("a.wikilink");
  if (wl) {
    e.preventDefault();
    try {
      const r = await api(`/api/resolve?target=${encodeURIComponent(wl.dataset.target)}&src=${encodeURIComponent(wl.dataset.from || "")}`);
      openNote(r.path);
    } catch (err) { toast(err.message, "err"); }
    return;
  }
  const row = e.target.closest("[data-open]");
  if (row) openNote(row.dataset.open);
});

async function refreshFolders() {
  try { S.folders = await api("/api/folders"); } catch { S.folders = []; }
}

// ================================================================ dashboard
async function loadDashboard(force = false) {
  if (S.stats && !force && S.loaded.dashboard) return;
  try {
    S.stats = await api(`/api/stats${force ? "?refresh=1" : ""}`);
    S.loaded.dashboard = true;
    renderDashboard();
  } catch (e) { toast(e.message, "err"); }
}
$("#btn-refresh-stats").onclick = (e) => busy(e.currentTarget, () => loadDashboard(true));

function renderDashboard() {
  const s = S.stats, t = s.totals;
  $("#dash-sub").textContent = `${s.vault} · ${fmt(t.notes)} ${plural(t.notes, "заметка", "заметки", "заметок")}`;
  const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
  const kpis = [
    ["Заметок", t.notes, `в ${t.folders} ${plural(t.folders, "папке", "папках", "папках")}`],
    ["Слов", t.words, `≈ ${fmt(t.avg_words)} на заметку`],
    ["Связей", t.links, `${t.avg_links} на заметку`],
    ["Тегов", t.tags, `${pct(t.notes_with_tags, t.notes)}% заметок с тегами`],
    ["Сирот", t.orphans, `${pct(t.orphans, t.notes)}% без связей`],
    ["Битых ссылок", t.broken, `${t.attachment_links} ссылок на вложения`],
    ["Вложений", t.attachments, "картинки, pdf и т.д."],
    ["Кластеров", t.components, `крупнейший: ${t.largest_component}`],
  ];
  $("#kpis").innerHTML = kpis.map(([l, v, sub]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${fmt(v)}</div><div class="s">${esc(sub)}</div></div>`).join("");

  Chart.defaults.color = "#9a9aab";
  Chart.defaults.borderColor = "#2e2e3a";
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

  const folders = s.top_folders.slice(0, 12);
  drawChart("folders", {
    type: "bar",
    data: { labels: folders.map((f) => f.folder), datasets: [{ data: folders.map((f) => f.notes), backgroundColor: folders.map((f) => colorFor(f.folder)), borderRadius: 4 }] },
    options: barOpts("y", (i) => folders[i].path !== undefined && openFolder(folders[i].path)),
  });
  const tags = s.tags.slice(0, 15);
  drawChart("tags", {
    type: "bar",
    data: { labels: tags.map((x) => "#" + x.tag), datasets: [{ data: tags.map((x) => x.count), backgroundColor: "#8b6cf6", borderRadius: 4 }] },
    options: barOpts("y", (i) => { showView("notes"); $("#notes-search").value = tags[i].tag; searchNotes(); }),
  });
  drawChart("growth", {
    type: "bar",
    data: {
      labels: s.growth.map((g) => g.month),
      datasets: [
        { type: "line", label: "Всего", data: s.growth.map((g) => g.total), borderColor: "#a78bfa", backgroundColor: "rgba(139,108,246,.12)", fill: true, tension: 0.3, pointRadius: 0, yAxisID: "y" },
        { type: "bar", label: "Добавлено", data: s.growth.map((g) => g.added), backgroundColor: "#38bdf8", borderRadius: 3, yAxisID: "y1" },
      ],
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { boxWidth: 10, boxHeight: 10 } } },
      scales: { x: { grid: { display: false } }, y: { beginAtZero: true, position: "left" }, y1: { beginAtZero: true, position: "right", grid: { display: false } } },
    },
  });

  renderHeatmap();
  const health = [
    ["Без входящих ссылок", t.no_backlinks, t.notes],
    ["Без исходящих ссылок", t.dead_ends, t.notes],
    ["Сироты", t.orphans, t.notes],
    ["Пустые (< 5 слов)", t.empty, t.notes],
    ["С тегами", t.notes_with_tags, t.notes],
    ["В главном кластере", t.largest_component, t.notes],
  ];
  $("#link-health").innerHTML = health.map(([l, v, total]) =>
    `<div class="health-item"><div class="v">${fmt(v)} <span class="muted small">/ ${fmt(total)}</span></div><div class="l">${l}</div><div class="bar"><div style="width:${pct(v, total)}%"></div></div></div>`).join("");

  listInto("#list-hubs", s.hubs);
  listInto("#list-linked", s.top_linked);
  listInto("#list-largest", s.largest, (v) => fmt(v));
  listInto("#list-orphans", s.orphans, null, `Сирот нет — всё связано`);
  $("#list-broken").innerHTML = s.broken.length
    ? s.broken.map((b) => `<div class="list-row" data-open="${esc(b.source)}"><span class="t">[[${esc(b.target)}]]<span class="sub">в ${esc(b.source)}</span></span></div>`).join("")
    : `<div class="list-empty">Битых ссылок нет</div>`;
  $("#list-unresolved").innerHTML = s.unresolved.length
    ? s.unresolved.map((u) => `<div class="list-row" data-create="${esc(u.target)}"><span class="t">${esc(u.target)}</span><span class="val">${u.count}</span></div>`).join("")
    : `<div class="list-empty">Нет</div>`;
  $$("#list-unresolved [data-create]").forEach((r) => (r.onclick = () => newNoteDialog(r.dataset.create, "")));

  renderFolderTable();
}

function barOpts(axis, onClick) {
  return {
    indexAxis: axis, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: { x: { beginAtZero: true, grid: { display: axis === "y" } }, y: { grid: { display: axis !== "y" }, ticks: { autoSkip: false } } },
    onClick: (_, els) => els.length && onClick(els[0].index),
    onHover: (e, els) => (e.native.target.style.cursor = els.length ? "pointer" : "default"),
  };
}

function drawChart(key, cfg) {
  S.charts[key]?.destroy();
  S.charts[key] = new Chart($(`#chart-${key}`), cfg);
}

function listInto(sel, items, valFmt, emptyText = "Нет данных") {
  $(sel).innerHTML = items.length
    ? items.map((i) => `<div class="list-row" data-open="${esc(i.path)}"><span class="t">${esc(i.title)}${i.folder ? `<span class="sub">${esc(i.folder)}</span>` : ""}</span>${i.value !== undefined ? `<span class="val">${valFmt ? valFmt(i.value) : i.value}</span>` : ""}</div>`).join("")
    : `<div class="list-empty">${emptyText}</div>`;
}

let folderSort = { key: "path", dir: 1 };
function renderFolderTable() {
  const cols = [["folder", "Папка"], ["notes", "Заметок", 1], ["direct", "Прямо в папке", 1], ["words", "Слов", 1], ["links", "Исходящих ссылок", 1]];
  const rows = [...S.stats.folders].sort((a, b) => {
    const k = folderSort.key === "folder" ? "path" : folderSort.key;
    return (a[k] > b[k] ? 1 : a[k] < b[k] ? -1 : 0) * folderSort.dir;
  });
  $("#table-folders").innerHTML =
    `<thead><tr>${cols.map(([k, l, n]) => `<th data-k="${k}" class="${n ? "num" : ""}">${l}${folderSort.key === k ? (folderSort.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.map((r) => `<tr data-folder="${esc(r.path)}" style="cursor:pointer"><td style="padding-left:${10 + Math.max(0, r.depth - 1) * 16}px">${esc(r.depth ? r.folder.split("/").pop() : r.folder)}</td><td class="num">${fmt(r.notes)}</td><td class="num">${fmt(r.direct)}</td><td class="num">${fmt(r.words)}</td><td class="num">${fmt(r.links)}</td></tr>`).join("")}</tbody>`;
  $$("#table-folders th").forEach((th) => (th.onclick = () => {
    folderSort = { key: th.dataset.k, dir: folderSort.key === th.dataset.k ? -folderSort.dir : th.dataset.k === "folder" ? 1 : -1 };
    renderFolderTable();
  }));
  $$("#table-folders tbody tr").forEach((tr) => (tr.onclick = () => openFolder(tr.dataset.folder)));
}

function openFolder(path) {
  showView("notes");
  $("#notes-search").value = "";
  $("#notes-folder").value = path || "";
  searchNotes();
}

$$("#heat-mode button").forEach((b) => (b.onclick = () => {
  S.heatMode = b.dataset.mode;
  $$("#heat-mode button").forEach((x) => x.classList.toggle("active", x === b));
  renderHeatmap();
}));

function renderHeatmap() {
  const data = S.stats.activity[S.heatMode] || {};
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const start = new Date(today);
  start.setDate(start.getDate() - 52 * 7 - ((today.getDay() + 6) % 7));
  const values = [];
  for (const d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) values.push(data[dayKey(d)] || 0);
  const nz = values.filter((v) => v > 0).sort((a, b) => a - b);
  const q = (p) => nz[Math.floor((nz.length - 1) * p)] || 1;
  const th = [q(0.25), q(0.5), q(0.8)];
  const level = (v) => (!v ? 0 : v <= th[0] ? 1 : v <= th[1] ? 2 : v <= th[2] ? 3 : 4);
  const months = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  let html = "", i = 0, total = 0, activeDays = 0;
  const d = new Date(start);
  while (d <= today) {
    // month label row
    const weekStart = new Date(d);
    const showMonth = weekStart.getDate() <= 7;
    html += `<div class="month">${showMonth ? months[weekStart.getMonth()] : ""}</div>`;
    for (let r = 0; r < 7; r++) {
      if (d > today) { html += `<div class="cell out"></div>`; continue; }
      const v = values[i++];
      total += v; if (v) activeDays++;
      html += `<div class="cell" data-l="${level(v)}" title="${d.toLocaleDateString("ru-RU")}: ${v} ${plural(v, "заметка", "заметки", "заметок")}"></div>`;
      d.setDate(d.getDate() + 1);
    }
  }
  const label = S.heatMode === "modified" ? "изменено" : "создано";
  $("#heatmap").innerHTML = `<div class="heatmap">${html}</div>
    <div class="heat-legend"><span>${fmt(total)} ${label} · ${activeDays} активных дней</span><span style="flex:1"></span>меньше
    ${[0, 1, 2, 3, 4].map((l) => `<span class="heatmap"><span class="cell" data-l="${l}"></span></span>`).join("")} больше</div>`;
}

// ================================================================ graph
async function loadGraph(force = false) {
  if (!S.graph || force) {
    try { S.graph = await api("/api/graph"); } catch (e) { return toast(e.message, "err"); }
  }
  const sel = $("#graph-folder");
  const tops = [...new Set(S.graph.nodes.map((n) => n.top))].sort();
  if (sel.options.length <= 1 || force) {
    sel.innerHTML = `<option value="">Все папки</option>` + tops.map((t) => `<option>${esc(t)}</option>`).join("");
  }
  renderGraph();
  requestAnimationFrame(fitGraph);
}

function renderGraph() {
  const g = S.graph;
  const folder = $("#graph-folder").value;
  const showOrphans = $("#graph-orphans").checked;
  const nodes = g.nodes.filter((n) => (!folder || n.top === folder) && (showOrphans || n.deg > 0)).map((n) => ({ ...n }));
  const ids = new Set(nodes.map((n) => n.id));
  const links = g.links.filter((l) => ids.has(l.source) && ids.has(l.target)).map((l) => ({ source: l.source, target: l.target }));
  const neighbors = new Map();
  for (const l of links) {
    if (!neighbors.has(l.source)) neighbors.set(l.source, new Set());
    if (!neighbors.has(l.target)) neighbors.set(l.target, new Set());
    neighbors.get(l.source).add(l.target);
    neighbors.get(l.target).add(l.source);
  }
  const maxDeg = Math.max(1, ...nodes.map((n) => n.deg));
  let hover = null;
  const el = $("#graph");

  if (!S.fg) {
    S.fg = ForceGraph()(el)
      .backgroundColor("rgba(0,0,0,0)")
      .nodeId("id")
      .linkDirectionalParticles(0)
      .cooldownTicks(200)
      .onEngineStop(() => { if (!S.graphFitted) { S.graphFitted = true; S.fg.zoomToFit(500, 50); } })
      .onNodeClick((n) => showGraphInfo(n))
      .onBackgroundClick(() => { $("#graph-info").classList.add("hidden"); S.graphFocus = null; });
    new ResizeObserver(fitGraph).observe(el);
    window.addEventListener("resize", fitGraph);
  }
  const search = () => $("#graph-search").value.trim().toLowerCase();
  const isDim = (n) => {
    const q = search();
    if (q && !n.name.toLowerCase().includes(q)) return true;
    const focus = hover || S.graphFocus;
    return focus && focus !== n.id && !neighbors.get(focus)?.has(n.id);
  };
  fitGraph();
  S.fg
    .nodeVal((n) => 1 + (n.deg / maxDeg) * 14)
    .nodeLabel((n) => `${esc(n.name)} — ${n.deg} ${plural(n.deg, "связь", "связи", "связей")}`)
    .nodeColor((n) => (isDim(n) ? "rgba(120,120,140,.18)" : colorFor(n.top)))
    .linkColor((l) => {
      const focus = hover || S.graphFocus;
      if (focus) return l.source.id === focus || l.target.id === focus ? "rgba(167,139,250,.8)" : "rgba(120,120,140,.05)";
      return "rgba(150,150,170,.2)";
    })
    .linkWidth((l) => ((hover || S.graphFocus) && (l.source.id === (hover || S.graphFocus) || l.target.id === (hover || S.graphFocus)) ? 1.6 : 0.6))
    .onNodeHover((n) => { hover = n ? n.id : null; el.style.cursor = n ? "pointer" : "default"; })
    .nodeCanvasObjectMode(() => "after")
    .nodeCanvasObject((n, ctx, scale) => {
      if (!$("#graph-labels").checked) return;
      const important = n.deg >= maxDeg * 0.35 || n.id === hover || n.id === S.graphFocus;
      if (!important && scale < 1.6) return;
      if (isDim(n) && n.id !== hover) return;
      const size = Math.max(10 / scale, 1.5);
      ctx.font = `${size}px Segoe UI, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = "rgba(228,228,234,.85)";
      const r = Math.sqrt(1 + (n.deg / maxDeg) * 14) * 4;
      ctx.fillText(n.name.length > 30 ? n.name.slice(0, 29) + "…" : n.name, n.x, n.y + r + 1);
    })
    .graphData({ nodes, links });
  S.fgNodes = nodes;
  S.graphFitted = false;

  const tops = [...new Set(nodes.map((n) => n.top))];
  $("#graph-legend").innerHTML = tops.sort().map((t) => `<div data-top="${esc(t)}"><i style="background:${colorFor(t)}"></i>${esc(t)}</div>`).join("") +
    `<div class="muted small" style="cursor:default;margin-top:4px">${nodes.length} заметок · ${links.length} связей</div>`;
  $$("#graph-legend [data-top]").forEach((d) => (d.onclick = () => { $("#graph-folder").value = $("#graph-folder").value === d.dataset.top ? "" : d.dataset.top; renderGraph(); }));
}

function fitGraph() {
  const el = $("#graph");
  if (S.fg && el.clientWidth && el.clientHeight) S.fg.width(el.clientWidth).height(el.clientHeight);
}

function showGraphInfo(n) {
  S.graphFocus = n.id;
  const box = $("#graph-info");
  const node = S.graph.nodes.find((x) => x.id === n.id);
  box.innerHTML = `<button class="close">×</button>
    <h4>${esc(node.name)}</h4><div class="muted small">${esc(node.folder || "(корень)")}</div>
    <div class="chips">${node.tags.map((t) => `<span class="chip">#${esc(t)}</span>`).join("")}</div>
    <div class="health" style="margin-top:12px">
      <div class="health-item"><div class="v">${node.in}</div><div class="l">входящих</div></div>
      <div class="health-item"><div class="v">${node.out}</div><div class="l">исходящих</div></div>
    </div>
    <div class="muted small" style="margin-top:8px">${fmt(node.words)} слов</div>
    <div class="row"><button class="btn accent small" data-open="${esc(node.id)}">Открыть заметку</button></div>`;
  box.classList.remove("hidden");
  box.querySelector(".close").onclick = () => { box.classList.add("hidden"); S.graphFocus = null; };
  S.fg.centerAt(n.x, n.y, 600);
  S.fg.zoom(Math.max(S.fg.zoom(), 2.5), 600);
}

$("#graph-folder").onchange = renderGraph;
$("#graph-orphans").onchange = renderGraph;
$("#graph-labels").onchange = () => S.fg && S.fg.nodeColor(S.fg.nodeColor());
$("#btn-graph-fit").onclick = () => S.fg?.zoomToFit(600, 40);
$("#graph-search").addEventListener("input", debounce(() => {
  if (!S.fg) return;
  S.fg.nodeColor(S.fg.nodeColor());
  const q = $("#graph-search").value.trim().toLowerCase();
  const matches = q ? (S.fgNodes || []).filter((n) => n.name.toLowerCase().includes(q)) : [];
  if (matches.length === 1) showGraphInfo(matches[0]);
}, 250));

// ================================================================ notes
async function loadNotes() {
  if (!S.loaded.notes) {
    await refreshFolders();
    fillFolderSelect($("#notes-folder"), "Все папки");
    S.loaded.notes = true;
    await searchNotes();
  }
}

async function searchNotes() {
  const q = $("#notes-search").value.trim();
  const folder = $("#notes-folder").value;
  try {
    S.notesList = await api(`/api/notes?q=${encodeURIComponent(q)}&folder=${encodeURIComponent(folder)}`);
  } catch (e) { return toast(e.message, "err"); }
  renderNotesList();
}

function renderNotesList() {
  const q = $("#notes-search").value.trim();
  const sort = $("#notes-sort").value;
  let list = [...S.notesList];
  if (!q) {
    const by = {
      mtime: (a, b) => b.mtime - a.mtime,
      title: (a, b) => a.title.localeCompare(b.title, "ru"),
      words: (a, b) => b.words - a.words,
      links: (a, b) => b.in + b.out - (a.in + a.out),
    }[sort];
    list.sort(by);
  }
  $("#notes-count").textContent = `${list.length} ${plural(list.length, "заметка", "заметки", "заметок")}${q ? " найдено" : ""}`;
  $("#notes-list").innerHTML = list.slice(0, 1500).map((n) => `
    <div class="note-item ${S.note?.path === n.path ? "active" : ""}" data-path="${esc(n.path)}">
      <div class="t">${esc(n.title)}</div>
      <div class="m"><span>${esc(n.folder || "/")}</span><span>${fmt(n.words)} сл.</span><span>↔ ${n.in + n.out}</span></div>
      ${n.snippet ? `<div class="snip">${esc(n.snippet)}</div>` : ""}
    </div>`).join("") || `<div class="list-empty">Ничего не найдено</div>`;
  $$("#notes-list .note-item").forEach((el) => (el.onclick = () => openNote(el.dataset.path)));
}

$("#notes-search").addEventListener("input", debounce(searchNotes, 250));
$("#notes-folder").onchange = searchNotes;
$("#notes-sort").onchange = renderNotesList;

async function openNote(path) {
  if (!$("#view-notes").classList.contains("active")) showView("notes");
  try { S.note = await api(`/api/note?path=${encodeURIComponent(path)}`); }
  catch (e) { return toast(e.message, "err"); }
  const n = S.note;
  $("#note-empty").classList.add("hidden");
  $("#note-view").classList.remove("hidden");
  $("#note-path").textContent = n.path;
  $("#note-title").textContent = n.title;
  $("#note-meta").textContent = `${fmt(n.words)} слов · создана ${fmtDate(n.created)} · изменена ${fmtDate(n.mtime)}`;
  $("#note-tags").innerHTML = n.tags.map((t) => `<span class="chip">#${esc(t)}</span>`).join("");
  $("#note-body").innerHTML = renderMarkdown(n.content, n.path);
  markUnresolved($("#note-body"), n.broken);
  $("#note-body").classList.remove("hidden");
  $("#note-editor").classList.add("hidden");
  $("#ai-panel").classList.add("hidden");
  $("#bl-count").textContent = n.backlinks.length;
  $("#ol-count").textContent = n.outlinks.length;
  listInto("#note-backlinks", n.backlinks, null, "Никто не ссылается на эту заметку");
  listInto("#note-outlinks", n.outlinks, null, "Нет ссылок на другие заметки");
  const vaultName = S.config.vault_name;
  $("#btn-note-obsidian").href = `obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(n.path.replace(/\.md$/, ""))}`;
  $$("#notes-list .note-item").forEach((el) => el.classList.toggle("active", el.dataset.path === n.path));
  $("#note-pane").scrollTop = 0;
}

function markUnresolved(root, broken) {
  const set = new Set((broken || []).map((b) => b.toLowerCase()));
  $$("a.wikilink", root).forEach((a) => { if (set.has(a.dataset.target.toLowerCase())) a.classList.add("unresolved"); });
}

async function reloadAfterChange(newPath) {
  S.loaded.dashboard = false;
  S.graph = null;
  await refreshFolders();
  fillFolderSelect($("#notes-folder"), "Все папки");
  await searchNotes();
  if (newPath) await openNote(newPath);
}

$("#btn-note-edit").onclick = () => {
  $("#note-textarea").value = S.note.content;
  $("#note-body").classList.add("hidden");
  $("#note-editor").classList.remove("hidden");
  $("#note-textarea").focus();
};
$("#btn-note-cancel").onclick = () => { $("#note-body").classList.remove("hidden"); $("#note-editor").classList.add("hidden"); };
$("#btn-note-save").onclick = (e) => busy(e.currentTarget, async () => {
  await api("/api/note/save", { body: { path: S.note.path, content: $("#note-textarea").value } });
  toast("Сохранено", "ok");
  await reloadAfterChange(S.note.path);
});
$("#note-textarea").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); $("#btn-note-save").click(); }
});

$("#btn-note-move").onclick = () => {
  modal("Переместить / переименовать", `
    <label>Новый путь</label><input id="m-path" value="${esc(S.note.path)}">
    <div class="muted small" style="margin-top:8px">Ссылки на заметку в других заметках обновятся автоматически (${S.note.backlinks.length}).</div>`, [
    { label: "Отмена" },
    { label: "Переместить", cls: "accent", onClick: async () => {
      try {
        const r = await api("/api/note/move", { body: { path: S.note.path, new_path: $("#m-path").value } });
        toast(`Перемещено. Ссылки обновлены в ${r.updated_links_in.length} заметках`, "ok");
        await reloadAfterChange(r.path);
      } catch (err) { toast(err.message, "err"); return false; }
    } },
  ]);
};

// ---------- templates
async function loadTemplates() {
  try { S.templates = await api("/api/templates"); }
  catch { S.templates = { folder: "", folder_map: {}, items: [] }; }
  return S.templates;
}
const tplByName = (name) => (S.templates?.items || []).find((t) => t.name === name);
function tplForFolder(folder) {
  // same rule as the server/Templater: the deepest configured parent folder wins
  let best = null;
  for (const [f, t] of Object.entries(S.templates?.folder_map || {})) {
    if ((folder === f || folder.startsWith(f + "/")) && (!best || f.length > best[0].length)) best = [f, t];
  }
  const items = S.templates?.items || [];
  if (best) return items.find((t) => t.path === best[1] || t.path === best[1] + ".md");
  return items.find((t) => t.folder && t.folder === folder);
}
const effectiveTemplate = (value, folder) => (value === "auto" ? tplForFolder(folder) : value ? tplByName(value) : null);

function fillTemplateSelect(sel, value = "auto") {
  sel.innerHTML = `<option value="auto">Шаблон: авто по папке</option><option value="">Без шаблона</option>` +
    (S.templates?.items || []).map((t) => `<option value="${esc(t.name)}">${esc(t.name)}</option>`).join("");
  sel.value = value;
}
function fillFolderPicker(sel, value = "") {
  const folders = [...new Set([...S.folders, ...(S.templates?.items || []).map((t) => t.folder).filter(Boolean)])].sort((a, b) => a.localeCompare(b, "ru"));
  sel.innerHTML = `<option value="">Папка: (корень)</option>` + folders.map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join("");
  sel.value = folders.includes(value) ? value : "";
}
function templateHint(t) {
  if (!t) return "Без шаблона — свободная заметка";
  const hint = (t.hint || "").split("\n").slice(0, 2).join(" ");
  return `Шаблон «${t.name}»${t.folder ? ` → ${t.folder}` : ""}${hint ? `\n${hint}` : ""}`;
}
// keeps template and folder selects consistent: picking a template moves to its folder
function linkTemplateFolder(tplSel, folderSel, onChange) {
  tplSel.onchange = () => {
    const t = tplByName(tplSel.value);
    if (t?.folder) fillFolderPicker(folderSel, t.folder);
    onChange();
  };
  folderSel.onchange = onChange;
  onChange();
}

async function newNoteDialog(title = "", folder = $("#notes-folder").value || "") {
  if (!S.templates) await loadTemplates();
  if (!S.folders.length) await refreshFolders();
  modal("Новая заметка", `
    <label>Название</label><input id="nn-title" value="${esc(title)}" placeholder="Например: Замыкание">
    <div class="grid-2">
      <div><label>Шаблон</label><select id="nn-template"></select></div>
      <div><label>Папка</label><select id="nn-folder"></select></div>
    </div>
    <div class="muted small" id="nn-hint" style="margin-top:8px;white-space:pre-line"></div>
    <div class="tpl-preview hidden" id="nn-preview"></div>`, [
    { label: "Отмена" },
    { label: "Создать", cls: "accent", onClick: async () => {
      try {
        const r = await api("/api/note/from-template", { body: {
          title: $("#nn-title").value, folder: $("#nn-folder").value, template: $("#nn-template").value } });
        toast(`Создано: ${r.path}`, "ok");
        await reloadAfterChange(r.path);
      } catch (err) { toast(err.message, "err"); return false; }
    } },
  ], "wide");
  fillTemplateSelect($("#nn-template"), "auto");
  fillFolderPicker($("#nn-folder"), folder);
  const preview = debounce(async () => {
    const t = effectiveTemplate($("#nn-template").value, $("#nn-folder").value);
    $("#nn-hint").textContent = templateHint(t);
    const box = $("#nn-preview");
    if (!t) return box.classList.add("hidden");
    try {
      const r = await api("/api/templates/render", { body: {
        title: $("#nn-title").value || "Без названия", folder: $("#nn-folder").value, template: $("#nn-template").value } });
      box.textContent = r.content.slice(0, 1500);
      box.classList.remove("hidden");
    } catch { box.classList.add("hidden"); }
  }, 200);
  linkTemplateFolder($("#nn-template"), $("#nn-folder"), preview);
  $("#nn-title").addEventListener("input", preview);
  $("#nn-title").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#modal-actions .accent").click(); });
}
$("#btn-new-note").onclick = () => newNoteDialog();

// ---------- AI on a note
function aiPanel(html) {
  const p = $("#ai-panel");
  p.innerHTML = `<button class="close" title="Закрыть">×</button>${html}`;
  p.classList.remove("hidden");
  p.querySelector(".close").onclick = () => p.classList.add("hidden");
  return p;
}

$("#btn-ai-summary").onclick = (e) => busy(e.currentTarget, async () => {
  const p = aiPanel(`<h4>Краткое содержание</h4><div class="markdown typing" id="ai-sum"></div>`);
  let text = "";
  const out = $("#ai-sum", p);
  await stream("/api/ai/summarize", { path: S.note.path }, (ev) => {
    if (ev.type === "token") { text += ev.content; out.innerHTML = renderMarkdown(text); }
  }).catch((err) => { out.innerHTML = `<span class="err-text">${esc(err.message)}</span>`; });
  out.classList.remove("typing");
});

$("#btn-ai-suggest").onclick = (e) => busy(e.currentTarget, async () => {
  aiPanel(`<h4>Анализирую заметку…</h4><div class="muted small">Локальная модель может думать 10–60 секунд.</div>`);
  let r;
  try { r = await api("/api/ai/suggest", { body: { path: S.note.path } }); }
  catch (err) { aiPanel(`<h4>Не получилось</h4><div class="err-text">${esc(err.message)}</div>`); return; }
  const tagsHtml = r.tags.length
    ? r.tags.map((t) => `<label class="chip"><input type="checkbox" data-tag="${esc(t)}" checked>#${esc(t)}</label>`).join("")
    : `<span class="muted small">новых тегов не предложено</span>`;
  const linksHtml = r.links.length
    ? r.links.map((l) => `<label class="chip plain"><input type="checkbox" data-link="${esc(l.path)}" checked>[[${esc(l.title)}]]</label>`).join("")
    : `<span class="muted small">подходящих связей не найдено</span>`;
  const others = r.candidates.filter((c) => !r.links.some((l) => l.path === c.path));
  const othersHtml = others.map((l) => `<label class="chip plain"><input type="checkbox" data-link="${esc(l.path)}">[[${esc(l.title)}]]</label>`).join("");
  const folderHtml = r.folder
    ? `<label class="chip plain"><input type="checkbox" id="sg-folder" checked> переместить в «${esc(r.folder)}»${r.folder_exists ? "" : " (новая папка)"}</label> <span class="muted small">сейчас: ${esc(r.current_folder || "/")}</span>`
    : `<span class="muted small">текущая папка «${esc(r.current_folder || "/")}» подходит</span>`;
  const p = aiPanel(`
    <h4>Предложения ИИ <span class="muted small">· ${esc(r.model)}</span></h4>
    ${r.reason ? `<div class="muted">${esc(r.reason)}</div>` : ""}
    <div class="section"><div class="section-label">Теги</div><div class="chips">${tagsHtml}</div></div>
    <div class="section"><div class="section-label">Ссылки на связанные заметки</div><div class="chips">${linksHtml}</div>
      ${othersHtml ? `<details style="margin-top:6px"><summary class="muted small">ещё кандидаты (${others.length})</summary><div class="chips">${othersHtml}</div></details>` : ""}</div>
    <div class="section"><div class="section-label">Папка</div>${folderHtml}</div>
    <div class="row"><button class="btn accent" id="sg-apply">Применить выбранное</button></div>`);
  $("#sg-apply", p).onclick = (ev) => busy(ev.currentTarget, async () => {
    const body = {
      path: S.note.path,
      tags: $$("[data-tag]:checked", p).map((x) => x.dataset.tag),
      links: $$("[data-link]:checked", p).map((x) => x.dataset.link),
      folder: $("#sg-folder", p)?.checked ? r.folder : "",
    };
    const res = await api("/api/ai/apply-suggestions", { body });
    toast(`Готово: тегов +${res.tags.length}, ссылок +${res.links.length}${res.moved ? ", заметка перемещена" : ""}`, "ok");
    await reloadAfterChange(res.path);
  });
});

$("#btn-ai-similar").onclick = (e) => busy(e.currentTarget, async () => {
  const r = await api(`/api/ai/similar?path=${encodeURIComponent(S.note.path)}`);
  const linked = new Set(S.note.outlinks.map((x) => x.path));
  const p = aiPanel(`<h4>Похожие заметки <span class="muted small">· ${r.semantic ? "по смыслу (индекс)" : "по словам — постройте индекс в настройках для поиска по смыслу"}</span></h4>
    <div class="list">${r.items.map((i) => `<div class="list-row" data-open="${esc(i.path)}"><span class="t">${esc(i.title)}<span class="sub">${esc(i.folder || "/")}</span></span>
      <span class="val">${i.score ? Math.round(i.score * 100) + "%" : ""}</span></div>`).join("") || `<div class="list-empty">Ничего не найдено</div>`}</div>`);
  if (!r.items.length) return;
  const unlinked = r.items.filter((i) => !linked.has(i.path)).slice(0, 5);
  if (unlinked.length) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<button class="btn small">Добавить ссылки на топ-${unlinked.length}</button>`;
    row.firstChild.onclick = (ev) => busy(ev.currentTarget, async () => {
      const res = await api("/api/ai/apply-suggestions", { body: { path: S.note.path, links: unlinked.map((u) => u.path) } });
      toast(`Добавлено ссылок: ${res.links.length}`, "ok");
      await reloadAfterChange(res.path);
    });
    p.appendChild(row);
  }
});

// ================================================================ chat
const METHOD_LABELS = { semantic: "по смыслу", hybrid: "по смыслу и словам", keyword: "по словам" };
const CHAT_EXAMPLES = ["О чём мои заметки в целом?", "Что я писал про ИИ?", "Какие у меня незаконченные идеи?", "Составь план на основе моих заметок о проекте"];

async function initChat() {
  if (!S.loaded.chat) {
    await refreshFolders();
    fillFolderSelect($("#chat-folder"), "Во всём хранилище");
    S.loaded.chat = true;
  }
  if (!S.chat.length) renderChatHint();
  $("#chat-input").focus();
}

function renderChatHint() {
  $("#chat-messages").innerHTML = `<div class="chat-hint">
    <h3>Спроси свою базу знаний</h3>
    <div>Модель найдёт подходящие заметки и ответит с опорой на них. Источники будут показаны под ответом.</div>
    <div class="examples">${CHAT_EXAMPLES.map((x) => `<button>${esc(x)}</button>`).join("")}</div></div>`;
  $$("#chat-messages .examples button").forEach((b) => (b.onclick = () => { $("#chat-input").value = b.textContent; sendChat(); }));
}

function addMsg(role, html) {
  $(".chat-hint")?.remove();
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = html;
  $("#chat-messages").appendChild(el);
  $("#chat-messages").scrollTop = $("#chat-messages").scrollHeight;
  return el;
}

async function sendChat() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text || S.chatAbort) return;
  input.value = "";
  S.chat.push({ role: "user", content: text });
  addMsg("user", esc(text));
  const el = addMsg("assistant", `<div class="markdown typing"></div><div class="meta"></div>`);
  const body = el.querySelector(".markdown"), meta = el.querySelector(".meta");
  let answer = "", sources = [], model = "", pending = false;
  const btn = $("#chat-send");
  btn.textContent = "Стоп";
  S.chatAbort = new AbortController();
  const box = $("#chat-messages");
  const paint = () => {
    pending = false;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    body.innerHTML = renderMarkdown(answer);
    if (atBottom) box.scrollTop = box.scrollHeight;
  };
  try {
    await stream("/api/chat", { messages: S.chat, use_rag: $("#chat-rag").checked, folder: $("#chat-folder").value }, (ev) => {
      if (ev.type === "sources") { sources = ev.sources; S.lastMethod = ev.method; }
      if (ev.type === "model") model = ev.model;
      if (ev.type === "token") { answer += ev.content; if (!pending) { pending = true; requestAnimationFrame(paint); } }
    }, S.chatAbort.signal);
  } catch (e) {
    if (e.name !== "AbortError") { el.classList.add("error"); answer += (answer ? "\n\n" : "") + "⚠ " + e.message; }
  } finally {
    S.chatAbort = null;
    btn.textContent = "Отправить";
  }
  paint();
  body.classList.remove("typing");
  if (answer && !el.classList.contains("error")) S.chat.push({ role: "assistant", content: answer });
  const method = METHOD_LABELS[S.lastMethod] || "по словам";
  meta.innerHTML = (model ? `<span>${esc(model)}</span>` : "") +
    (sources.length ? `<span>· источники (${method}):</span>` + [...new Map(sources.map((s) => [s.path, s])).values()]
      .map((s) => `<span class="src" data-open="${esc(s.path)}">${esc(s.title)}</span>`).join("") : "");
}

$("#chat-form").onsubmit = (e) => {
  e.preventDefault();
  if (S.chatAbort) { S.chatAbort.abort(); return; }
  sendChat();
};
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); }
});
$("#btn-chat-clear").onclick = () => { S.chatAbort?.abort(); S.chat = []; renderChatHint(); };

// ================================================================ tools / agent
const AGENT_EXAMPLES = [
  "Создай заметки-концепты по шаблону для терминов, на которые есть ссылки, но нет заметок",
  "Разбери папку 000 Inbox: разложи заметки по подходящим папкам",
  "Создай MOC по всем заметкам про Python",
  "Свяжи заметки-сироты с подходящими заметками",
];

async function loadTools() {
  await refreshFolders();
  const sel = $("#sum-folder");
  const cur = sel.value;
  sel.innerHTML = `<option value="">Всё хранилище</option>` + S.folders.map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join("");
  sel.value = cur;
  if (!S.templates) await loadTemplates();
  if (!S.loaded.gen) {
    fillTemplateSelect($("#gen-template"), "auto");
    fillFolderPicker($("#gen-folder"), "000 Inbox");
    linkTemplateFolder($("#gen-template"), $("#gen-folder"), () => {
      $("#gen-template-hint").textContent = templateHint(effectiveTemplate($("#gen-template").value, $("#gen-folder").value));
    });
    S.loaded.gen = true;
  }
  $("#agent-examples").innerHTML = AGENT_EXAMPLES.map((x) => `<button>${esc(x)}</button>`).join("");
  $$("#agent-examples button").forEach((b) => (b.onclick = () => { $("#agent-input").value = b.textContent; }));
}

const ACTION_LABELS = { create: "создать", create_folder: "папка", append: "дописать", move: "переместить", add_tags: "теги", add_links: "ссылки", trash: "в корзину" };

$("#btn-agent-plan").onclick = (e) => busy(e.currentTarget, async () => {
  const instruction = $("#agent-input").value.trim();
  if (!instruction) return toast("Опиши задачу для агента");
  $("#agent-plan").innerHTML = `<div class="plan-expl muted">Агент думает… Локальной модели может понадобиться до пары минут.</div>`;
  let plan;
  try { plan = await api("/api/agent/plan", { body: { instruction } }); }
  catch (err) { $("#agent-plan").innerHTML = `<div class="plan-expl err-text">${esc(err.message)}</div>`; return; }
  renderPlan(plan);
});

function renderPlan(plan) {
  const box = $("#agent-plan");
  const acts = plan.actions;
  box.innerHTML = `<div class="plan-expl"><b>План</b> <span class="muted small">· ${esc(plan.model)}</span><div>${esc(plan.explanation || "—")}</div></div>` +
    (acts.length ? "" : `<div class="muted">Агент не предложил изменений.</div>`) +
    acts.map((a, i) => `
      <div class="action ${a.ok ? "" : "bad"}" data-i="${i}">
        <div class="action-head">
          <input type="checkbox" ${a.ok ? "checked" : "disabled"}>
          <span class="action-type ${esc(a.type)}">${esc(ACTION_LABELS[a.type] || a.type)}</span>
          <span class="action-summary">${esc(a.summary)}${a.type === "move" && a.backlinks ? ` <span class="muted small">(обновятся ссылки: ${a.backlinks})</span>` : ""}</span>
          ${a.content !== undefined ? `<button class="btn small" data-toggle>Текст</button>` : ""}
        </div>
        ${a.brief ? `<div class="action-brief">Задача: ${esc(a.brief)}</div>` : ""}
        ${a.warning ? `<div class="action-warn">⚠ ${esc(a.warning)}</div>` : ""}
        ${a.content !== undefined ? `<div class="action-body hidden"><textarea rows="${a.template ? 16 : 8}">${esc(a.content)}</textarea></div>` : ""}
        ${a.error ? `<div class="action-err">${esc(a.error)}</div>` : ""}
      </div>`).join("") +
    (acts.some((a) => a.ok) ? `<div class="row"><button class="btn accent" id="btn-agent-apply">Применить выбранное</button><button class="btn" id="btn-agent-cancel">Отменить</button></div>` : "") +
    `<div class="results" id="agent-results"></div>`;
  $$("[data-toggle]", box).forEach((b) => (b.onclick = () => b.closest(".action").querySelector(".action-body").classList.toggle("hidden")));
  $("#btn-agent-cancel", box) && ($("#btn-agent-cancel", box).onclick = () => (box.innerHTML = ""));
  const applyBtn = $("#btn-agent-apply", box);
  if (applyBtn) applyBtn.onclick = (e) => busy(e.currentTarget, async () => {
    const selected = $$(".action", box).filter((el) => el.querySelector("input").checked).map((el) => {
      const a = { ...acts[+el.dataset.i] };
      const ta = el.querySelector("textarea");
      if (ta) a.content = ta.value;
      return a;
    });
    if (!selected.length) return toast("Ничего не выбрано");
    const r = await api("/api/agent/apply", { body: { actions: selected } });
    $("#agent-results").innerHTML = r.results.map((x) => `<div class="result ${x.ok ? "ok" : "fail"}">${esc(x.summary)} — <span class="muted">${esc(x.message)}</span></div>`).join("");
    applyBtn.disabled = true;
    const okCount = r.results.filter((x) => x.ok).length;
    toast(`Выполнено действий: ${okCount} из ${r.results.length}`, okCount === r.results.length ? "ok" : "err");
    S.loaded.dashboard = false; S.loaded.notes = false; S.graph = null;
  });
}

$("#btn-gen").onclick = (e) => busy(e.currentTarget, async () => {
  const prompt = $("#gen-prompt").value.trim();
  const title = $("#gen-title").value.trim();
  const folder = $("#gen-folder").value, template = $("#gen-template").value;
  const tpl = effectiveTemplate(template, folder);
  if (!prompt && !title) return toast("Напиши название или о чём заметка");
  if (tpl && !title) return toast("Для заметки по шаблону нужно название");
  const out = $("#gen-output");
  out.value = "";
  out.classList.remove("hidden");
  $("#gen-save-row").classList.add("hidden");
  $("#gen-sources").textContent = "";
  let final = null;
  await stream("/api/ai/generate", { prompt: prompt || title, use_context: $("#gen-context").checked, template, title, folder }, (ev) => {
    if (ev.type === "sources") $("#gen-sources").textContent = "Контекст: " + ev.sources.map((s) => s.title).join(", ");
    if (ev.type === "token") { out.value += ev.content; out.scrollTop = out.scrollHeight; }
    if (ev.type === "final") final = ev.content;
  });
  if (final !== null) out.value = final;
  if (!title) {
    const h = out.value.match(/^#\s+(.+)$/m);
    $("#gen-title").value = (h ? h[1] : prompt.slice(0, 50)).replace(/[\\/:*?"<>|#]/g, "").trim();
  }
  $("#gen-save-path").textContent = genPath();
  $("#gen-save-row").classList.remove("hidden");
});

const genPath = () => {
  const folder = $("#gen-folder").value, title = $("#gen-title").value.trim();
  return `${folder ? folder + "/" : ""}${title}.md`;
};

$("#btn-gen-save").onclick = (e) => busy(e.currentTarget, async () => {
  const r = await api("/api/note/create", { body: { path: genPath(), content: $("#gen-output").value } });
  toast(`Создано: ${r.path}`, "ok");
  S.loaded.notes = false; S.loaded.dashboard = false; S.graph = null;
  $("#gen-save-row").classList.add("hidden");
});

$("#btn-sum-folder").onclick = (e) => busy(e.currentTarget, async () => {
  const out = $("#sum-output");
  out.innerHTML = "";
  out.classList.add("typing");
  let text = "";
  try {
    await stream("/api/ai/summarize", { folder: $("#sum-folder").value }, (ev) => {
      if (ev.type === "token") { text += ev.content; out.innerHTML = renderMarkdown(text); }
    });
  } finally { out.classList.remove("typing"); }
});

// ================================================================ settings
async function loadSettings() {
  const c = S.config;
  $("#set-vault").value = c.vault_path || "";
  $("#set-ollama-url").value = c.ollama_url || "";
  $("#set-chat-model").value = c.chat_model || "";
  $("#set-embed-model").value = c.embed_model || "";
  $("#set-temperature").value = c.temperature;
  $("#set-chunks").value = c.context_chunks;
  $("#set-ctx").value = c.context_length || 8192;
  S.provider = c.chat_provider || "ollama";
  $("#set-cloud-preset").innerHTML = Object.entries(c.presets || {}).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("");
  $("#set-cloud-preset").value = c.cloud_preset || "openrouter";
  fillCloudFields($("#set-cloud-preset").value);
  renderProviderUI();
  try {
    const vaults = await api("/api/vaults/detected");
    $("#detected-vaults").innerHTML = vaults.length
      ? `<div class="muted small">Найдены хранилища Obsidian:</div>` + vaults.map((v) => `
        <div class="vault-opt"><div><b>${esc(v.name)}</b><div class="p">${esc(v.path)}</div></div>
        <button class="btn small" data-vault="${esc(v.path)}">${v.path === c.vault_path ? "Подключено" : "Выбрать"}</button></div>`).join("")
      : "";
    $$("[data-vault]").forEach((b) => (b.onclick = () => { $("#set-vault").value = b.dataset.vault; $("#btn-save-vault").click(); }));
  } catch { /* ignore */ }
  checkOllama(true);
  checkLLM(true);
  refreshIndexStatus();
}

// ---------- chat provider (local Ollama / cloud API)
function renderProviderUI() {
  $$("#provider-seg button").forEach((b) => b.classList.toggle("active", b.dataset.p === S.provider));
  $("#prov-ollama").classList.toggle("hidden", S.provider !== "ollama");
  $("#prov-cloud").classList.toggle("hidden", S.provider !== "cloud");
  $("#prov-manual").classList.toggle("hidden", S.provider !== "manual");
}
$$("#provider-seg button").forEach((b) => (b.onclick = () => { S.provider = b.dataset.p; renderProviderUI(); $("#llm-info").innerHTML = ""; }));

function fillCloudFields(preset) {
  const c = S.config, prof = c.cloud?.[preset] || {}, meta = c.presets?.[preset] || {};
  $("#set-cloud-url").value = prof.base_url || meta.base_url || "";
  $("#set-cloud-key").value = "";
  $("#set-cloud-key").placeholder = prof.has_key ? `ключ сохранён (${prof.key_hint}) — оставь пустым, чтобы не менять` : "вставь API-ключ";
  $("#btn-clear-key").classList.toggle("hidden", !prof.has_key);
  $("#set-cloud-model").value = prof.model || "";
  $("#set-cloud-model").placeholder = meta.example ? `например: ${meta.example}` : "имя модели";
  const link = $("#cloud-keys-link");
  link.textContent = meta.keys_url ? "· получить ключ ↗" : "";
  link.href = meta.keys_url || "#";
  $("#cloud-free-wrap").classList.toggle("hidden", preset !== "openrouter");
  $("#models-cloud").innerHTML = "";
}
$("#set-cloud-preset").onchange = () => { fillCloudFields($("#set-cloud-preset").value); $("#llm-info").innerHTML = ""; };
$("#cloud-free-only").onchange = () => fillCloudModels();

function fillCloudModels() {
  const free = $("#cloud-free-only").checked;
  const models = (S.cloudModels || []).filter((m) => !free || m.free);
  $("#models-cloud").innerHTML = models.map((m) => `<option value="${esc(m.name)}">${m.free ? "бесплатно" : ""}</option>`).join("");
}

async function checkLLM(detailed = false) {
  let st;
  try { st = await api("/api/llm/status"); } catch (e) { st = { ok: false, error: e.message, models: [], label: "Чат" }; }
  S.llm = st;
  $("#llm-dot").className = `dot ${st.ok ? "ok" : "err"}`;
  $("#llm-label").textContent = `${st.label}: ${st.model || "модель не выбрана"}`;
  if (!detailed) return;
  if (st.provider === "cloud") {
    S.cloudModels = st.models;
    fillCloudModels();
  }
  const where = st.provider === "cloud" ? st.label : "Ollama";
  let html;
  if (st.ok) html = `<div class="ok-text" style="margin-top:8px">✓ ${esc(where)} готов, модель <code>${esc(st.model)}</code>${st.provider === "cloud" ? ` · доступно моделей: ${st.models.length}` : ""}</div>`;
  else if (st.provider === "cloud" && st.models.length) html = `<div class="warn-text" style="margin-top:8px">Ключ принят (моделей: ${st.models.length}). ${esc(st.error)}</div>`;
  else html = `<div class="err-text" style="margin-top:8px">✕ ${esc(st.error || "не готов")}</div>`;
  if (st.ok && st.provider === "cloud" && st.models.length && !st.models.some((m) => m.name === st.model)) {
    html += `<div class="warn-text small">Модели «${esc(st.model)}» нет в списке провайдера — проверь название.</div>`;
  }
  $("#llm-info").innerHTML = html;
}

$("#btn-clear-key").onclick = (e) => busy(e.currentTarget, async () => {
  S.config = await api("/api/config", { body: { cloud_preset: $("#set-cloud-preset").value, clear_api_key: true } });
  fillCloudFields($("#set-cloud-preset").value);
  toast("Ключ удалён", "ok");
  checkLLM(true);
});

$("#btn-save-vault").onclick = (e) => busy(e.currentTarget, async () => {
  S.config = await api("/api/config", { body: { vault_path: $("#set-vault").value } });
  Object.assign(S, { stats: null, graph: null, note: null, notesList: [], loaded: {}, colorMap: {}, templates: null });
  loadTemplates();
  S.fg?.graphData({ nodes: [], links: [] });
  $("#note-view").classList.add("hidden");
  $("#note-empty").classList.remove("hidden");
  updateVaultLabel();
  toast(`Хранилище «${S.config.vault_name}» подключено`, "ok");
  loadSettings();
});

$("#btn-save-ai").onclick = (e) => busy(e.currentTarget, async () => {
  const body = {
    chat_provider: S.provider,
    chat_model: $("#set-chat-model").value.trim(),
    temperature: parseFloat($("#set-temperature").value) || 0,
    context_chunks: parseInt($("#set-chunks").value, 10) || 6,
    context_length: parseInt($("#set-ctx").value, 10) || 8192,
  };
  if (S.provider === "cloud") Object.assign(body, {
    cloud_preset: $("#set-cloud-preset").value,
    cloud_base_url: $("#set-cloud-url").value.trim(),
    cloud_api_key: $("#set-cloud-key").value.trim(),
    cloud_model: $("#set-cloud-model").value.trim(),
  });
  S.config = await api("/api/config", { body });
  fillCloudFields($("#set-cloud-preset").value);
  toast("Настройки сохранены", "ok");
  await checkLLM(true);
});

$("#btn-save-ollama").onclick = (e) => busy(e.currentTarget, async () => {
  S.config = await api("/api/config", { body: {
    ollama_url: $("#set-ollama-url").value.trim(),
    embed_model: $("#set-embed-model").value.trim(),
  } });
  toast("Настройки Ollama сохранены", "ok");
  checkOllama(true);
  checkLLM();
  refreshIndexStatus();
});

$("#btn-check-ollama").onclick = (e) => busy(e.currentTarget, async () => {
  S.config = await api("/api/config", { body: { ollama_url: $("#set-ollama-url").value.trim() } });
  await checkOllama(true);
});

async function checkOllama(detailed = false) {
  let st;
  try { st = await api("/api/ollama/status"); } catch (e) { st = { ok: false, error: e.message, models: [] }; }
  S.ollama = st;
  $("#ollama-dot").className = `dot ${st.ok ? "ok" : "err"}`;
  $("#ollama-label").textContent = st.ok ? `Ollama ${st.version} · ${st.models.length} ${plural(st.models.length, "модель", "модели", "моделей")}` : "Ollama недоступна";
  if (!detailed) return;
  const chat = st.models.filter((m) => !m.embedding), emb = st.models.filter((m) => m.embedding);
  $("#models-chat").innerHTML = chat.map((m) => `<option value="${esc(m.name)}">`).join("");
  $("#models-embed").innerHTML = emb.map((m) => `<option value="${esc(m.name)}">`).join("");
  const gb = (b) => (b / 1e9).toFixed(1) + " ГБ";
  let html;
  if (st.ok) {
    html = `<div class="ok-text" style="margin-top:8px">✓ Подключено к Ollama ${esc(st.version)}</div>` +
      (st.models.length ? `<div class="model-list">${st.models.map((m) => `<span class="chip ${m.embedding ? "plain" : ""}" title="${gb(m.size)}">${esc(m.name)}${m.params ? " · " + esc(m.params) : ""}${m.embedding ? " · эмбеддинги" : ""}</span>`).join("")}</div>` : `<div class="warn-text">Моделей нет — скачай хотя бы одну (см. инструкцию ниже).</div>`);
    const warn = [];
    if (!chat.length) warn.push("нет чат-модели");
    if (!emb.length) warn.push("нет модели эмбеддингов (поиск будет по словам)");
    if (warn.length) html += `<div class="warn-text small" style="margin-top:6px">Внимание: ${warn.join("; ")}</div>`;
  } else {
    html = `<div class="err-text" style="margin-top:8px">✕ ${esc(st.error || "Ollama недоступна")}</div>`;
  }
  $("#ollama-info").innerHTML = html;
  $("#ollama-help").classList.toggle("hidden", st.ok && chat.length > 0 && emb.length > 0);
}

let indexTimer = null;
async function refreshIndexStatus() {
  let st;
  try { st = await api("/api/index/status"); } catch { return; }
  const el = $("#index-status");
  const when = st.updated ? new Date(st.updated * 1000).toLocaleString("ru-RU") : "никогда";
  let html = st.ready
    ? `<div>Проиндексировано <b>${fmt(st.notes)}</b> заметок (${fmt(st.chunks)} фрагментов), модель <code>${esc(st.model)}</code>, обновлено ${when}.</div>`
    : `<div class="warn-text">Индекс ещё не построен.</div>`;
  if (st.ready && st.stale) html += `<div class="warn-text small">Изменилось заметок с момента индексации: ${st.stale}</div>`;
  if (st.ready && st.embed_model && st.model !== st.embed_model) html += `<div class="warn-text small">Выбрана другая модель эмбеддингов — индекс будет пересоздан.</div>`;
  if (st.state === "error") html += `<div class="err-text small">Ошибка: ${esc(st.error)}</div>`;
  if (st.state === "running") html += `<div class="muted small">Индексация: ${fmt(st.done)} / ${fmt(st.total)} фрагментов…</div>`;
  el.innerHTML = html;
  const prog = $("#index-progress");
  prog.classList.toggle("hidden", st.state !== "running");
  prog.firstElementChild.style.width = st.total ? `${(st.done / st.total) * 100}%` : "0";
  $("#btn-build-index").disabled = $("#btn-rebuild-index").disabled = st.state === "running";
  clearTimeout(indexTimer);
  if (st.state === "running") indexTimer = setTimeout(refreshIndexStatus, 1000);
  else if (S.indexWasRunning) { toast(st.state === "done" ? "Индекс обновлён" : "Индексация не удалась", st.state === "done" ? "ok" : "err"); }
  S.indexWasRunning = st.state === "running";
}

async function buildIndex(full) {
  try {
    await api("/api/index/build", { body: { full } });
    S.indexWasRunning = true;
    refreshIndexStatus();
  } catch (e) { toast(e.message, "err"); }
}
$("#btn-build-index").onclick = () => buildIndex(false);
$("#btn-rebuild-index").onclick = () => buildIndex(true);

// ================================================================ quick find / ask (Ctrl+K)
const PAL = { results: [], sel: 0, seq: 0, abort: null, method: "" };
const QUESTION_RE = /^(где|что|когда|как|какой|какая|какое|какие|почему|зачем|кто|сколько|есть ли|напомни|вспомни|найди|покажи|расскажи|объясни)(\s|$)/i;
const isQuestion = (q) => /[?？]\s*$/.test(q) || QUESTION_RE.test(q);
const palOpen = () => !$("#palette").classList.contains("hidden");

function openPalette(prefill = "") {
  if (!S.config?.vault_ok) return toast("Сначала подключи хранилище в настройках");
  $("#palette").classList.remove("hidden");
  const inp = $("#pal-input");
  inp.value = prefill;
  inp.focus();
  inp.select();
  $("#pal-answer").classList.add("hidden");
  PAL.results = [];
  PAL.sel = 0;
  prefill ? palSearch() : renderPalette();
}
function closePalette() {
  PAL.abort?.abort();
  $("#palette").classList.add("hidden");
}

function highlight(text, q) {
  let html = esc(text);
  const words = q.toLowerCase().match(/[\p{L}\p{N}_]{4,}/gu) || [];
  for (const w of words) {
    const stem = w.length > 5 ? w.slice(0, Math.max(4, w.length - 2)) : w;
    html = html.replace(new RegExp(`(${stem.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "giu"), "<mark>$1</mark>");
  }
  return html;
}

function renderPalette() {
  const q = $("#pal-input").value.trim();
  const box = $("#pal-results");
  $("#pal-method").textContent = PAL.method ? `поиск ${METHOD_LABELS[PAL.method] || ""}` : "";
  if (!q) {
    box.innerHTML = `<div class="pal-empty">Напиши, что ищешь: название, тему или вопрос.<br>Например: «docker compose», «что я решил по проекту?», «где заметка про замыкания»</div>`;
    return;
  }
  const ask = `<div class="pal-item ask ${PAL.sel === 0 ? "sel" : ""}" data-i="0">
      <svg class="ico" viewBox="0 0 24 24"><path d="M12 3l1.8 4.7L18.5 9l-4.7 1.8L12 15.5l-1.8-4.7L5.5 9l4.7-1.3z"/></svg>
      <div class="body"><div class="t">Спросить ИИ: «${esc(q)}»</div><div class="snip">Короткий ответ по твоим заметкам со ссылками на источники</div></div><kbd>Ctrl ↵</kbd></div>`;
  const rows = PAL.results.map((r, i) => `
    <div class="pal-item ${PAL.sel === i + 1 ? "sel" : ""}" data-i="${i + 1}">
      <svg class="ico" viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5"/></svg>
      <div class="body"><div class="t">${highlight(r.title, q)}<span class="f">${esc(r.folder || "/")}</span></div>
      ${r.snippet ? `<div class="snip">${highlight(r.snippet, q)}</div>` : ""}</div></div>`).join("");
  box.innerHTML = ask + (rows || `<div class="pal-empty">Заметок не найдено — попробуй спросить ИИ</div>`);
  $$(".pal-item", box).forEach((el) => {
    el.onmouseenter = () => { PAL.sel = +el.dataset.i; $$(".pal-item", box).forEach((x) => x.classList.toggle("sel", x === el)); };
    el.onclick = () => palActivate(+el.dataset.i);
  });
  box.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
}

async function palSearch() {
  const q = $("#pal-input").value.trim();
  const seq = ++PAL.seq;
  if (!q) { PAL.results = []; PAL.method = ""; return renderPalette(); }
  let r;
  try { r = await api(`/api/search?q=${encodeURIComponent(q)}&k=12`); }
  catch (e) { r = { results: [], method: "" }; toast(e.message, "err"); }
  if (seq !== PAL.seq) return;  // a newer query already started
  PAL.results = r.results;
  PAL.method = r.method;
  PAL.sel = isQuestion(q) || !r.results.length ? 0 : 1;
  renderPalette();
}

function palActivate(i) {
  if (i === 0) return palAsk();
  const r = PAL.results[i - 1];
  if (r) { closePalette(); openNote(r.path); }
}

async function palAsk() {
  const q = $("#pal-input").value.trim();
  if (!q) return;
  PAL.abort?.abort();
  PAL.abort = new AbortController();
  const box = $("#pal-answer");
  box.classList.remove("hidden");
  box.innerHTML = `<div class="label">ОТВЕТ ИИ</div><div class="markdown typing"></div><div class="meta"></div>`;
  const body = box.querySelector(".markdown"), meta = box.querySelector(".meta");
  let answer = "", sources = [], model = "";
  try {
    await stream("/api/ask", { question: q }, (ev) => {
      if (ev.type === "sources") sources = ev.sources;
      if (ev.type === "model") model = ev.model;
      if (ev.type === "token") { answer += ev.content; body.innerHTML = renderMarkdown(answer); }
    }, PAL.abort.signal);
  } catch (e) {
    if (e.name === "AbortError") return;
    answer += (answer ? "\n\n" : "") + "⚠ " + e.message;
    body.innerHTML = renderMarkdown(answer);
  }
  body.classList.remove("typing");
  const uniq = [...new Map(sources.map((s) => [s.path, s])).values()];
  meta.innerHTML = (model ? `<span>${esc(model)}</span>` : "") +
    (uniq.length ? `<span>· искал в:</span>` + uniq.map((s) => `<span class="src" data-open="${esc(s.path)}">${esc(s.title)}</span>`).join("") : "");
}

$("#btn-open-palette").onclick = () => openPalette();
$("#pal-input").addEventListener("input", debounce(palSearch, 300));
$("#pal-input").addEventListener("keydown", (e) => {
  const n = PAL.results.length + 1;
  if (e.key === "ArrowDown") { e.preventDefault(); PAL.sel = (PAL.sel + 1) % n; renderPalette(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); PAL.sel = (PAL.sel - 1 + n) % n; renderPalette(); }
  else if (e.key === "Enter") {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) palAsk();
    else palActivate(PAL.sel);
  }
});
$("#palette").addEventListener("click", (e) => {
  if (e.target.id === "palette") return closePalette();
  // links in the answer open notes via the global handler; just get out of the way
  if (e.target.closest("[data-open], a.wikilink")) closePalette();
});
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.code === "KeyK") {
    e.preventDefault();
    palOpen() ? closePalette() : openPalette();
  } else if (e.key === "Escape" && palOpen()) {
    closePalette();
  }
});

// ================================================================ boot
function updateVaultLabel() {
  $("#vault-name").textContent = S.config.vault_ok ? S.config.vault_name : "не выбрано";
}

async function boot() {
  try { S.config = await api("/api/config"); }
  catch (e) { toast("Сервер недоступен: " + e.message, "err"); return; }
  updateVaultLabel();
  if (S.config.vault_ok) loadTemplates();
  checkOllama();
  checkLLM();
  setInterval(() => checkOllama(), 30000);
  const last = localStorage.getItem("view") || "dashboard";
  showView(S.config.vault_ok ? last : "settings");
}
boot();
