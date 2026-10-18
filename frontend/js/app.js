/* Strata Deck frontend */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden", "err");
  if (err) t.classList.add("err");
  setTimeout(() => t.classList.add("hidden"), 4000);
}

const fmtGB = (b) => (b / 2 ** 30).toFixed(1) + " GiB";
const hwEsc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n) => (n == null ? "—" : n);
const fmtCtx = (n) => {
  if (n == null) return "—";
  if (n >= 1048576) return (n / 1048576).toFixed(n % 1048576 ? 1 : 0) + "M";
  if (n >= 1024) return (n / 1024).toFixed(0) + "K";
  return String(n);
};
const fmtUptime = (s) => {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600),
        m = Math.floor(s % 3600 / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m ${Math.floor(s % 60)}s`;
};

/* ------------------------------------------------------------- tabs */
$$(".tab").forEach((b) =>
  b.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.remove("active"));
    $$(".tabpage").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("#tab-" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "models") loadModels();
    if (b.dataset.tab === "tuner") loadTuner();
    if (b.dataset.tab === "logs") loadLogs();
    if (b.dataset.tab === "install") loadInstallForm();
  })
);

/* ---------------------------------------------------------- dashboard */
let engineRunning = false;

async function refreshStatus() {
  try {
    const [st, sys] = await Promise.all([api("/api/status"), api("/api/system")]);
    engineRunning = st.running;
    const pill = $("#engine-pill");
    pill.textContent = st.running
      ? "engine: " + (st.process?.model || "running")
      : "engine: stopped";
    pill.className = "pill " + (st.running ? "on" : "off");
    engineInUse = st.in_use || null;
    lastConsumers = st.consumers || [];
    let useEl = $("#inuse-pill");
    if (st.running && engineInUse) {
      if (!useEl) {
        useEl = document.createElement("span");
        useEl.id = "inuse-pill";
        useEl.className = "pill on";
        pill.after(useEl);
      }
      const a = engineInUse;
      const who = (st.consumers || []).map((c) => c.host).join(", ");
      useEl.textContent = a.in_flight > 0
        ? `IN USE: ${a.in_flight} request(s) in flight`
        : `IN USE: last request ${a.last_request_age_s}s ago`;
      if (who) useEl.textContent += " — " + who;
      useEl.style.borderColor = "var(--red)";
      useEl.style.color = "var(--red)";
    } else if (useEl) {
      useEl.remove();
    }
    $("#btn-power").className = "power " + (st.running ? "on" : "off");
    $("#power-label").textContent = st.running
      ? `running — ${st.process.model} (pid ${st.process.pid})`
      : "engine stopped";

    // Strata's built-in chat + monitor live on the engine's own port;
    // use the browser's host so links work from any machine on the network
    const engBase = `http://${location.hostname}:${st.port || 8080}`;
    // Strata's built-in UI is a single-page app on the engine port:
    // / = chat, /#monitor = monitor, /#about = about. Always alive.
    for (const [sel, path] of [["#link-chat", "/"], ["#link-monitor", "/#monitor"], ["#link-about", "/#about"]]) {
      $(sel).href = engBase + path;
    }

    let info = "—";
    if (st.api) {
      const a = st.api;
      info = `<b>model:</b> ${fmt(a.model_name || a.model)}<br>` +
             `<b>ctx:</b> ${fmtCtx(a.context?.max_positions ?? a.context?.native ?? a.max_context ?? a.n_ctx)}<br>` +
             `<b>uptime:</b> ${fmtUptime(a.uptime_s ?? a.uptime_seconds ?? a.uptime)}`;
      $("#timings").textContent = JSON.stringify(
        a.timings || a.last_timings || a, null, 2).slice(0, 4000);
    } else if (st.error) {
      info = `<span class="muted">${st.error}</span>`;
    }
    $("#eng-info").innerHTML = info;

    if (sys.gpu) {
      const g = sys.gpu;
      $("#gpu-info").innerHTML =
        `<b>${g.name}</b><br>${g.mem_used_mib} / ${g.mem_total_mib} MiB · util ${g.util_pct}%`;
      setBar("#gpu-bar", (g.mem_used_mib / g.mem_total_mib) * 100);
    }
    if (sys.ram_hw) {
      const hw = $("#ram-hw");
      hw.innerHTML = `<b>${hwEsc(sys.ram_hw.summary)}</b>`;
      hw.title = sys.ram_hw.dimms.map((d) =>
        `${d.locator}: ${d.size} @ ${d.speed}${d.vendor ? " · " + d.vendor : ""}${d.part ? " · " + d.part : ""}`).join("\n");
    }
    if (sys.ram) {
      if (sys.ram.model != null) {
        // attribution: model (engine+proxy RSS) vs rest of system vs installed
        $("#ram-info").innerHTML =
          `<b>${fmtGB(sys.ram.model)}</b> model · <b>${fmtGB(sys.ram.system)}</b> system` +
          `<br>${fmtGB(sys.ram.used)} used / ${fmtGB(sys.ram.total)} installed`;
        setBar("#ram-bar", (sys.ram.used / sys.ram.total) * 100);
        const rb = $("#ram-bar");
        rb.style.setProperty("--model-pct", (sys.ram.model / sys.ram.total) * 100 + "%");
        rb.classList.add("stacked");
      } else {
        $("#ram-info").innerHTML = `${fmtGB(sys.ram.used)} / ${fmtGB(sys.ram.total)}`;
        setBar("#ram-bar", (sys.ram.used / sys.ram.total) * 100);
      }
    }
    const disk = sys.disk_data || sys.disk_root;
    if (disk) {
      $("#disk-info").innerHTML =
        `<b>${sys.data_dir || "/"}</b><br>${fmtGB(disk.used)} used · ${fmtGB(disk.free)} free`;
      setBar("#disk-bar", (disk.used / disk.total) * 100);
    }
  } catch (e) {
    $("#engine-pill").textContent = "deck: backend unreachable";
  }
}
function setBar(sel, pct) {
  const b = $(sel);
  b.style.width = Math.min(100, pct) + "%";
  b.className = "bar-fill" + (pct > 92 ? " hot" : pct > 80 ? " warn" : "");
}

$("#btn-power").addEventListener("click", async () => {
  try {
    if (engineRunning) { await api("/api/stop", { method: "POST" }); toast("engine stopped"); }
    else {
      const models = await api("/api/models");
      const first = models.installed[0];
      if (!first) return toast("no installed models", true);
      await api("/api/start", { method: "POST", body: JSON.stringify({ model: first.model }) });
      toast(`starting ${first.model}…`);
    }
  } catch (e) { toast(e.message, true); }
  setTimeout(refreshStatus, 2500);
});

let engineInUse = null;
let lastConsumers = [];

$("#btn-restart").addEventListener("click", async () => {
  let force = false;
  if (engineInUse) {
    const a = engineInUse;
    const who = (lastConsumers || []).map((c) => c.host).join(", ") || "unknown client";
    const msg = a.in_flight > 0
      ? `The engine is BUSY RIGHT NOW (${a.in_flight} request(s) in flight).\n\nActive consumers: ${who}\n\nRestarting WILL interrupt their sessions.\n\nForce restart anyway?`
      : `The engine saw traffic ${a.last_request_age_s}s ago.\n\nRecent consumers: ${who}\n\nRestart anyway?`;
    if (!confirm(msg)) return;
    force = true;
  } else if (!confirm("Restart the engine?\n\nThis stops the model that may be serving live agent conversations — any in-flight agent turn will be interrupted.")) return;
  try { await api("/api/restart", { method: "POST", body: JSON.stringify({ force }) }); toast("restarting…"); }
  catch (e) { toast(e.message, true); }
  setTimeout(refreshStatus, 4000);
});

/* ------------------------------------------------------------- models */
const FAMILY_PREFIXES = ["swift-", "unsloth-", "coder-"];
const normKey = (s) => {
  let k = (s || "").toLowerCase();
  for (const p of FAMILY_PREFIXES) k = k.replace(p, "");
  return k.replace(/[-_]/g, "");
};

async function loadModels() {
  const m = await api("/api/models");
  const g = $("#models-grid");
  g.innerHTML = "";
  // index installed configs by normalized key: iq3_s<->IQ3_S, swift-iq3_xxs<->IQ3_XXS
  const byKey = {};
  for (const c of m.installed) byKey[normKey(c.model)] = c;

  const cards = [];
  const aboutText = {};   // model -> full about text, for the About modal
  // one card per installed config (variants get their own card:
  // iq3_xxs and swift-iq3_xxs render separately, UI stays balanced)
  const covered = new Set();
  for (const c of m.installed) {
    const cat = m.available.find((a) => normKey(a.model) === normKey(c.model));
    covered.add(normKey(c.model));
    const d = document.createElement("div");
    d.className = "mcard" + (c.stale ? " stale" : "");
    const badges = cat ? [
      cat.download_gb ? `<span class="badge">${cat.download_gb} GB dl</span>` : "",
      cat.arena_gb ? `<span class="badge">${cat.arena_gb} GB experts</span>` : "",
      ...cat.families.map((f) => `<span class="badge">${f}</span>`),
      cat.experimental ? '<span class="badge amber">experimental</span>' : "",
      cat.vision ? '<span class="badge green">vision</span>' : "",
    ].join("") : "";
    const about = (cat && cat.about) || c.model_name || "";
    aboutText[c.model] = about;
    const staleList = c.stale
      ? `<div class="stale-list">${c.missing_paths.map((p) => `<div class="stale-path">${p.flag} → ${p.path}</div>`).join("")}</div>`
      : "";
    d.innerHTML = `<h4>${c.model} <span class="badge green">Installed</span>
        ${c.stale ? '<span class="badge red">⚠ stale paths</span>' : ""}</h4>
      <div class="badge-row">${badges}</div>
      <div class="meta">port ${fmt(c.port)} · ctx ${fmtCtx(c.max_context)} · kv ${fmt(c.kv)}</div>
      ${staleList}
      <div class="mcard-actions"><button class="btn ${c.stale ? "danger" : "primary"}" data-start="${c.model}" data-stale="${c.stale ? 1 : 0}">Start</button>
      <button class="btn" data-tune="${c.model}">Configure</button>
      <button class="badge about" data-about="${c.model}" title="Read the full description">ℹ About</button></div>`;
    cards.push(d);
  }
  // catalog entries with no installed variant at all
  for (const a of m.available) {
    if (covered.has(normKey(a.model))) continue;
    const d = document.createElement("div");
    d.className = "mcard";
    aboutText[a.model] = a.about || "";
    d.innerHTML = `<h4>${a.model} <span class="badge">not installed</span></h4>
      <div class="badge-row">
      ${a.download_gb ? `<span class="badge">${a.download_gb} GB dl</span>` : ""}
      ${a.arena_gb ? `<span class="badge">${a.arena_gb} GB experts</span>` : ""}
      ${a.families.map((f) => `<span class="badge">${f}</span>`).join("")}
      ${a.experimental ? '<span class="badge amber">experimental</span>' : ""}
      ${a.vision ? '<span class="badge green">vision</span>' : ""}</div>
      <div class="mcard-actions"><button class="btn primary" data-install="${a.model}" data-family="${a.families[0] || "qwen"}">Install…</button>
      <button class="badge about" data-about="${a.model}" title="Read the full description">ℹ About</button></div>`;
    cards.push(d);
  }
  // installed first, then not-installed (Start button = installed marker)
  cards.sort((x, y) => (y.querySelector("[data-start]") ? 1 : 0) -
                       (x.querySelector("[data-start]") ? 1 : 0));
  cards.forEach((c) => g.appendChild(c));

  g.querySelectorAll("[data-about]").forEach((b) =>
    b.addEventListener("click", () => {
      const model = b.dataset.about;
      $("#about-title").textContent = model;
      $("#about-body").textContent = aboutText[model] || "(no description available)";
      $("#about-modal").classList.remove("hidden");
    }));
  $("#about-close").addEventListener("click", () => $("#about-modal").classList.add("hidden"));
  $("#about-modal").addEventListener("click", (e) => {
    if (e.target.id === "about-modal") e.currentTarget.classList.add("hidden");
  });

  g.querySelectorAll("[data-start]").forEach((b) =>
    b.addEventListener("click", async () => {
      const model = b.dataset.start;
      const force = b.dataset.stale === "1";
      if (force &&
          !confirm("This config points at missing files (stale paths). It will likely fail to start.\n\nRe-run: ./setup.sh --data-dir <path> for this model.\n\nStart anyway (force)?")) return;
      try {
        // /api/switch validates the target config BEFORE stopping the
        // running engine; it no-ops if the model is already running.
        const sw = await api("/api/switch", { method: "POST", body: JSON.stringify({ model, force }) });
        if (sw.switched === null) { toast(sw.note); setTimeout(refreshStatus, 500); return; }
        await api("/api/start", { method: "POST", body: JSON.stringify({ model, force }) });
        toast(`starting ${model}…`);
        setTimeout(refreshStatus, 3000);
      } catch (e) { toast(e.message, true); }
    }));
  g.querySelectorAll("[data-tune]").forEach((b) =>
    b.addEventListener("click", () => {
      $$(".tab").forEach((x) => x.classList.remove("active"));
      $$(".tabpage").forEach((x) => x.classList.remove("active"));
      document.querySelector('[data-tab="tuner"]').classList.add("active");
      $("#tab-tuner").classList.add("active");
      loadTuner(b.dataset.tune);
    }));
  g.querySelectorAll("[data-install]").forEach((b) =>
    b.addEventListener("click", () => {
      $$(".tab").forEach((x) => x.classList.remove("active"));
      $$(".tabpage").forEach((x) => x.classList.remove("active"));
      document.querySelector('[data-tab="install"]').classList.add("active");
      $("#tab-install").classList.add("active");
      loadInstallForm(b.dataset.install, b.dataset.family);
    }));
}

/* ------------------------------------------------------------ install */
let installJob = null;
async function loadInstallForm(preselect, prefamily) {
  const m = await api("/api/models");
  const sel = $("#inst-model");
  sel.innerHTML = "";
  for (const a of m.available) {
    const o = document.createElement("option");
    o.value = a.model; o.textContent = a.model;
    sel.appendChild(o);
  }
  if (preselect) sel.value = preselect;
  if (prefamily) $("#inst-family").value = prefamily;
}

$("#install-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    model: $("#inst-model").value,
    family: $("#inst-family").value,
    vision: $("#inst-vision").value,
  };
  if ($("#inst-context").value) body.context = +$("#inst-context").value;
  try {
    const r = await api("/api/install", { method: "POST", body: JSON.stringify(body) });
    installJob = r.job_id;
    $("#install-log").classList.remove("hidden");
    pollInstall();
  } catch (err) { toast(err.message, true); }
});

async function pollInstall() {
  if (!installJob) return;
  try {
    const s = await api("/api/install/" + installJob);
    $("#install-log").textContent = s.log_tail.join("\n");
    $("#install-log").scrollTop = 1e9;
    if (s.done) {
      toast(s.rc === 0 ? `install of ${s.model} finished` : `install failed (rc ${s.rc})`, s.rc !== 0);
      installJob = null;
      return;
    }
  } catch (e) { installJob = null; return; }
  setTimeout(pollInstall, 2000);
}

/* -------------------------------------------------------------- tuner */
async function loadTuner(preselect) {
  const m = await api("/api/models");
  const sel = $("#tune-model");
  sel.innerHTML = "";
  for (const c of m.installed) {
    const o = document.createElement("option");
    o.value = c.model; o.textContent = c.model;
    sel.appendChild(o);
  }
  if (preselect) sel.value = preselect;
  sel.onchange = fillTuner;
  fillTuner();
}

const TUNE_PORTS = [8080, 8000, 8081, 8082, 8086, 8888, 5000, 5060, 5080, 5090, 3060, 3080, 3090];

async function fillTuner() {
  const model = $("#tune-model").value;
  if (!model) return;
  const [c, p] = await Promise.all([
    api("/api/config/" + model), api("/api/ports")]);
  // port dropdown: exclude ports in use, EXCEPT this model's own current
  // port (editing the running model's config is legitimate) and 8090 (deck)
  const ps = $("#tune-port");
  ps.innerHTML = "";
  const taken = new Set(p.listening);
  for (const port of TUNE_PORTS) {
    if (taken.has(port) && port !== (c.port ?? 8080)) continue;
    const o = document.createElement("option");
    o.value = port; o.textContent = String(port);
    ps.appendChild(o);
  }
  ps.value = c.port ?? 8080;
  $("#tune-host").value = c.host ?? "127.0.0.1";
  const args = c.args || [];
  const idx = (f) => args.indexOf(f);
  const ctx = idx("--max-context") >= 0 ? args[idx("--max-context") + 1] : "";
  const ctxSel = $("#tune-ctx");
  if (![...ctxSel.options].some((o) => o.value === String(ctx))) {
    const o = document.createElement("option");
    o.value = ctx; o.textContent = ctx + " (custom)";
    ctxSel.appendChild(o);
  }
  ctxSel.value = String(ctx);
  const kv = idx("--kv") >= 0 ? args[idx("--kv") + 1] : "int8";
  const kvSel = $("#tune-kv");
  if (![...kvSel.options].some((o) => o.value === kv)) {
    const o = document.createElement("option");
    o.value = kv; o.textContent = kv + " (custom)";
    kvSel.appendChild(o);
  }
  kvSel.value = kv;
  $("#tune-args").value = args.join("\n");
}

$("#btn-save-config").addEventListener("click", async () => {
  const model = $("#tune-model").value;
  const body = {
    port: +$("#tune-port").value,
    host: $("#tune-host").value,
    max_context: +$("#tune-ctx").value || null,
    kv: $("#tune-kv").value || null,
    args: $("#tune-args").value.split("\n").map((s) => s.trim()).filter(Boolean),
  };
  try {
    await api("/api/config/" + model, { method: "PUT", body: JSON.stringify(body) });
    $("#tune-msg").textContent = "saved (.bak written) — restart engine to apply";
    toast("config saved");
  } catch (e) { toast(e.message, true); }
});

/* --------------------------------------------------------------- logs */
async function loadLogs() {
  const m = await api("/api/models");
  const sel = $("#log-model");
  sel.innerHTML = "";
  for (const c of m.installed) {
    const o = document.createElement("option");
    o.value = c.model; o.textContent = c.model;
    sel.appendChild(o);
  }
  showLogs();
}
async function showLogs() {
  const model = $("#log-model").value;
  if (!model) return;
  const r = await api("/api/logs/" + model + "?lines=400");
  const v = $("#log-view");
  v.textContent = r.lines.join("\n");
  v.scrollTop = 1e9;
}
$("#btn-refresh-logs").addEventListener("click", showLogs);

/* -------------------------------------------------------------- boot */
refreshStatus();
setInterval(refreshStatus, 5000);
