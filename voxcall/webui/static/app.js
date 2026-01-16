let currentConfig = null;
let dirtyPatch = {};
let dirtyTimer = null;
let toastObj = null;

function showToast(message, variant = "success") {
    const toastEl = el("saveToast");
    const bodyEl = el("saveToastBody");
    if (!toastEl || !bodyEl) return;

    bodyEl.textContent = message;

    // swap bootstrap contextual class
    toastEl.classList.remove("text-bg-success", "text-bg-danger", "text-bg-warning", "text-bg-info", "text-bg-secondary");
    toastEl.classList.add(`text-bg-${variant}`);

    if (!toastObj) toastObj = new bootstrap.Toast(toastEl, { delay: 1600 });
    toastObj.show();
}

const el = (id) => document.getElementById(id);

el("btnSave").addEventListener("click", async () => {
    const payload = dirtyPatch;
    dirtyPatch = {};
    if (dirtyTimer) clearTimeout(dirtyTimer);

    try {
        if (payload && Object.keys(payload).length) {
            await apiPost("/api/config", payload, "PATCH");
        } else {
            await apiPost("/api/config/save"); // optional endpoint
        }
    } catch (e) {
        console.error(e);
    }
});

function fmt3(n) {
  n = Math.max(0, Math.min(999, Number(n || 0)));
  return String(n).padStart(3, "0");
}

function setLed(id, on, onClass) {
  const node = el(id);
  node.classList.remove("text-bg-secondary", "text-bg-success", "text-bg-danger");
  node.classList.add(on ? onClass : "text-bg-secondary");
}

function setSigBar(pct, open) {
  pct = Math.max(0, Math.min(100, Number(pct || 0)));
  el("sigBar").style.width = `${pct}%`;
  el("sigBar").classList.toggle("bg-success", !!open);
  el("sigBar").classList.toggle("bg-info", !open);
}

function setThresholdMarker(thr) {
    thr = Math.max(0, Math.min(100, Number(thr || 0)));
    el("thrMarker").style.setProperty("--thr", (thr / 100).toString());
}

function applyState(s) {
  el("statusText").textContent = s.status_text || "";
  el("levelPct").textContent = fmt3(s.level_pct);
  el("levelDb").textContent = (s.level_db === null || s.level_db === undefined) ? "" : `${Number(s.level_db).toFixed(1)} dB`;

  setLed("ledRx", !!s.led_rx, "text-bg-success");
  // rec on: green by default (you can swap to danger depending on your engine semantics)
  setLed("ledRec", !!s.led_rec, "text-bg-danger");

  setSigBar(s.level_pct, s.led_rx);

  el("sqlSlider").value = Number(s.sql_threshold || 0);
  el("sqlValue").textContent = fmt3(s.sql_threshold);
  setThresholdMarker(s.sql_threshold);

  // buttons
  el("btnStart").disabled = !!s.running;
  el("btnStop").disabled = !s.running;
}

async function apiGet(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(await r.text());
  return await r.json();
}

async function apiPost(path, body=null, method="POST") {
  const r = await fetch(path, {
    method,
    headers: body ? {"Content-Type":"application/json"} : {},
    body: body ? JSON.stringify(body) : null,
  });
  if (!r.ok) throw new Error(await r.text());
  return await r.json();
}

function scheduleAutosave(patchObj) {
  // shallow merge into dirtyPatch
  dirtyPatch = mergeDeep(dirtyPatch, patchObj);

  if (dirtyTimer) clearTimeout(dirtyTimer);
  dirtyTimer = setTimeout(async () => {
    const payload = dirtyPatch;
    dirtyPatch = {};
    try {
      await apiPost("/api/config", payload, "PATCH");
      showToast("Autosaved", "success");
    } catch (e) {
      console.error(e);
    }
  }, 400);
}

function mergeDeep(target, src) {
  const out = structuredClone(target);
  for (const [k, v] of Object.entries(src)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      out[k] = mergeDeep(out[k] || {}, v);
    } else {
      out[k] = v;
    }
  }
  return out;
}

// ---- tabs ----
function initTabs() {
  document.querySelectorAll("#tabs a.nav-link").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      document.querySelectorAll("#tabs a.nav-link").forEach(x => x.classList.remove("active"));
      a.classList.add("active");

      const tab = a.getAttribute("data-tab");
      document.querySelectorAll(".tabpane").forEach(p => p.classList.add("d-none"));
      el(`tab-${tab}`).classList.remove("d-none");
    });
  });
}

// ---- forms (render from config) ----
function inputRow(label, inputHtml, hint="") {
    return `
  <div class="row g-3 align-items-center mb-3">
    <div class="col-sm-3 col-form-label text-body-secondary">${label}</div>
    <div class="col-sm-9">
      ${inputHtml}
      ${hint ? `<div class="form-text text-body-secondary">${hint}</div>` : ""}
    </div>
  </div>`;
}

async function renderConfig() {
  currentConfig = await apiGet("/api/config");
  const devices = await apiGet("/api/devices");

  // General
  el("tab-general").innerHTML = `
    <h5 class="mb-3">Behavior</h5>
    ${inputRow(
      "Archive audio files",
      `<div class="form-check form-switch">
         <input class="form-check-input" type="checkbox" id="saveAudio">
       </div>`
    )}
    ${inputRow(
      "Archive folder",
      `<input class="form-control" id="archiveDir" placeholder="/path/to/archive">`
    )}
    ${inputRow(
      "MP3 bitrate (bps)",
      `<input class="form-control" id="mp3Bitrate" type="number" min="8000" step="1000">`,
      "Example: 32000"
    )}
  `;

  // Audio
  const deviceOptions = (devices.devices || []).map(d => `<option value="${d}">${d}</option>`).join("");
  el("tab-audio").innerHTML = `
    <h5 class="mb-3">Input</h5>
    ${inputRow(
      "Device",
      `<select class="form-select" id="deviceName">${deviceOptions}</select>`
    )}
    ${inputRow(
      "Channel",
      `<select class="form-select" id="inChannel">
         <option value="mono">mono</option>
         <option value="left">left</option>
         <option value="right">right</option>
       </select>`
    )}

    <hr class="border-secondary-subtle my-4">

    <h5 class="mb-3">Detection Tuning</h5>
    ${inputRow("rectime (sec)", `<input class="form-control" id="rectime" type="number" step="0.01" min="0.01">`, "How often we sample audio")}
    ${inputRow("Silence stop (sec)", `<input class="form-control" id="silenceStop" type="number" step="0.1" min="0">`)}
    ${inputRow("Timeout (sec)", `<input class="form-control" id="timeoutSec" type="number" step="1" min="0">`)}
  `;

  // Broadcastify
  el("tab-bcfy").innerHTML = `
    <h5 class="mb-3">Broadcastify</h5>
    ${inputRow("API Key", `<input class="form-control" id="bcfyKey">`)}
    ${inputRow("System ID", `<input class="form-control" id="bcfySysid">`)}
    ${inputRow("Slot ID", `<input class="form-control" id="bcfySlot">`)}
    ${inputRow("Freq (MHz)", `<input class="form-control" id="bcfyFreq">`)}
    <div class="text-body-secondary small">Blank API Key = Broadcastify uploads skipped.</div>
  `;

  // rdio-scanner
  el("tab-rdio").innerHTML = `
    <h5 class="mb-3">rdio-scanner</h5>
    ${inputRow("API URL", `<input class="form-control" id="rdioUrl">`)}
    ${inputRow("API Key", `<input class="form-control" id="rdioKey">`)}
    ${inputRow("System ID", `<input class="form-control" id="rdioSys">`)}
    ${inputRow("Talkgroup", `<input class="form-control" id="rdioTg">`)}
    <div class="text-body-secondary small">If any field is blank, rdio-scanner upload is skipped.</div>
  `;

  // iCad Dispatch
  el("tab-icad").innerHTML = `
    <h5 class="mb-3">iCad Dispatch</h5>
    ${inputRow("API URL", `<input class="form-control" id="icadUrl">`)}
    ${inputRow("API Key", `<input class="form-control" id="icadKey">`)}
    ${inputRow("System ID", `<input class="form-control" id="icadSys">`)}
    ${inputRow("Talkgroup", `<input class="form-control" id="icadTg">`)}
    <div class="text-body-secondary small">If any field is blank, iCad Dispatch upload is skipped.</div>
  `;

  // OpenMHz
  el("tab-openmhz").innerHTML = `
    <h5 class="mb-3">OpenMHz</h5>
    ${inputRow("API Key", `<input class="form-control" id="omhzKey">`)}
    ${inputRow("Short Name", `<input class="form-control" id="omhzShort">`)}
    ${inputRow("TGID", `<input class="form-control" id="omhzTgid">`)}
    <div class="text-body-secondary small">OpenMHz uses Broadcastify Freq for upload metadata.</div>
  `;

  // populate + bind
  bindConfigToUI(devices);
}

function bindConfigToUI(devices) {
  // LIVE slider => config patch
  const sql = el("sqlSlider");
  sql.addEventListener("input", () => {
    const v = Number(sql.value || 0);
    el("sqlValue").textContent = fmt3(v);
    setThresholdMarker(v);
    scheduleAutosave({audio: {record_threshold: v}});
  });

  // General
  el("saveAudio").checked = !!currentConfig.general.save_audio;
  el("archiveDir").value = currentConfig.general.archive_dir || "";
  el("mp3Bitrate").value = Number(currentConfig.general.mp3_bitrate || 32000);

  el("saveAudio").addEventListener("change", (e) => scheduleAutosave({general:{save_audio: e.target.checked}}));
  el("archiveDir").addEventListener("input", (e) => scheduleAutosave({general:{archive_dir: e.target.value}}));
  el("mp3Bitrate").addEventListener("input", (e) => scheduleAutosave({general:{mp3_bitrate: Number(e.target.value || 0)}}));

  // Audio
  const deviceName = el("deviceName");
  const currentName = devices.current || "";
  if (currentName) deviceName.value = currentName;

  el("inChannel").value = currentConfig.audio.in_channel || "mono";
  el("rectime").value = Number(currentConfig.audio.rectime || 0.1);
  el("silenceStop").value = Number(currentConfig.audio.vox_silence_time || 2.0);
  el("timeoutSec").value = Number(currentConfig.audio.timeout_time_sec || 120);

  deviceName.addEventListener("change", async (e) => {
    // map name -> index by asking server to patch via device index is easiest
    // for now, just tell server we picked by name isn't supported; better: add endpoint to map name->index.
    // simplest: server already has mapping; patch by name isn't implemented here.
    // quick workaround: reload /api/devices with ordering = indexes and use selected index.
    const idx = deviceName.selectedIndex; // assumes list order matches indices (often true)
    scheduleAutosave({audio:{device_index: idx}});
  });

  el("inChannel").addEventListener("change", (e) => scheduleAutosave({audio:{in_channel: e.target.value}}));
  el("rectime").addEventListener("input", (e) => scheduleAutosave({audio:{rectime: Number(e.target.value)}}));
  el("silenceStop").addEventListener("input", (e) => scheduleAutosave({audio:{vox_silence_time: Number(e.target.value)}}));
  el("timeoutSec").addEventListener("input", (e) => scheduleAutosave({audio:{timeout_time_sec: Number(e.target.value)}}));

  // Broadcastify
  el("bcfyKey").value = currentConfig.bcfy.api_key || "";
  el("bcfySysid").value = currentConfig.bcfy.system_id || "";
  el("bcfySlot").value = currentConfig.bcfy.slot_id || "1";
  el("bcfyFreq").value = currentConfig.bcfy.freq_mhz || "";

  el("bcfyKey").addEventListener("input", (e) => scheduleAutosave({bcfy:{api_key: e.target.value}}));
  el("bcfySysid").addEventListener("input", (e) => scheduleAutosave({bcfy:{system_id: e.target.value}}));
  el("bcfySlot").addEventListener("input", (e) => scheduleAutosave({bcfy:{slot_id: e.target.value}}));
  el("bcfyFreq").addEventListener("input", (e) => scheduleAutosave({bcfy:{freq_mhz: e.target.value}}));

  // rdio
  el("rdioUrl").value = currentConfig.rdio.api_url || "";
  el("rdioKey").value = currentConfig.rdio.api_key || "";
  el("rdioSys").value = currentConfig.rdio.system || "";
  el("rdioTg").value = currentConfig.rdio.talkgroup || "";

  el("rdioUrl").addEventListener("input", (e) => scheduleAutosave({rdio:{api_url: e.target.value}}));
  el("rdioKey").addEventListener("input", (e) => scheduleAutosave({rdio:{api_key: e.target.value}}));
  el("rdioSys").addEventListener("input", (e) => scheduleAutosave({rdio:{system: e.target.value}}));
  el("rdioTg").addEventListener("input", (e) => scheduleAutosave({rdio:{talkgroup: e.target.value}}));

  // iCad Dispatch
  const icad = currentConfig.icad_dispatch || {}; // backend should expose this key

  el("icadUrl").value = icad.api_url || "";
  el("icadKey").value = icad.api_key || "";
  el("icadSys").value = icad.system || "";
  el("icadTg").value = icad.talkgroup || "";

  el("icadUrl").addEventListener("input", (e) => scheduleAutosave({icad_dispatch:{api_url: e.target.value}}));
  el("icadKey").addEventListener("input", (e) => scheduleAutosave({icad_dispatch:{api_key: e.target.value}}));
  el("icadSys").addEventListener("input", (e) => scheduleAutosave({icad_dispatch:{system: e.target.value}}));
  el("icadTg").addEventListener("input", (e) => scheduleAutosave({icad_dispatch:{talkgroup: e.target.value}}));

  // openmhz
  el("omhzKey").value = currentConfig.openmhz.api_key || "";
  el("omhzShort").value = currentConfig.openmhz.short_name || "";
  el("omhzTgid").value = currentConfig.openmhz.tgid || "";

  el("omhzKey").addEventListener("input", (e) => scheduleAutosave({openmhz:{api_key: e.target.value}}));
  el("omhzShort").addEventListener("input", (e) => scheduleAutosave({openmhz:{short_name: e.target.value}}));
  el("omhzTgid").addEventListener("input", (e) => scheduleAutosave({openmhz:{tgid: e.target.value}}));
}

function initButtons() {
  el("btnStart").addEventListener("click", async () => {
    const r = await apiPost("/api/engine/start");
    applyState(r.state);
  });
  el("btnStop").addEventListener("click", async () => {
    const r = await apiPost("/api/engine/stop");
    applyState(r.state);
  });
  el("btnSave").addEventListener("click", async () => {
    const btn = el("btnSave");

    // force-save current dirty patch immediately
    const payload = dirtyPatch;
    dirtyPatch = {};
    if (dirtyTimer) clearTimeout(dirtyTimer);

    // tiny UX: disable button while saving
    btn.disabled = true;

    try {
      if (payload && Object.keys(payload).length) {
        await apiPost("/api/config", payload, "PATCH");
      } else {
        // if you keep the endpoint; otherwise just toast "Nothing to save"
        await apiPost("/api/config/save");
      }
      showToast("Saved ✓", "success");
    } catch (e) {
      console.error(e);
      showToast("Save failed (check console/logs)", "danger");
    } finally {
      btn.disabled = false;
    }
  });
}

function initSSE() {
  const es = new EventSource("/api/events");
  es.addEventListener("state", (evt) => {
    try { applyState(JSON.parse(evt.data)); } catch {}
  });
  es.addEventListener("config", (evt) => {
    // optional: if server pushes config changes, you could re-render
  });
  es.onerror = () => {
    // browsers auto-retry; keep quiet
  };
}

(async function main() {
  initTabs();
  initButtons();

  const st = await apiGet("/api/state");
  applyState(st);

  await renderConfig();
  initSSE();
})();
