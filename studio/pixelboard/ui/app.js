"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

const layout = { chain: 1, split: false, offset_x: 0, offset_y: 0, scale: 1.0, brightness: 90, swap: false };
const preview = { frames: [], width: 64, height: 32, index: 0, playing: true, timer: null, lut: null };
let hasGif = false;
let busy = false;
let auditionIndex = -1;   // track being auditioned, -1 when stopped
let musicState = null;

// Transitional (Task 6): the backend now takes a placement dict and keeps the panel arrangement
// itself; Task 7 replaces the chain/swap controls with the arrangement editor.
function placement() {
  return {
    mode: layout.split ? "split" : "whole", offset_x: layout.offset_x, offset_y: layout.offset_y,
    scale: layout.scale, brightness: layout.brightness,
  };
}

async function setPanelCount(n) {
  const res = await api().set_panel_count(n);
  if (!res.ok) { setStatus(res.message, "error"); return; }
  update({ chain: res.arrangement.panels.length });
}

const KIND_TEXT = {
  first_install: "首次安裝：會寫入完整韌體、GIF 和音樂",
  update_firmware: "更新韌體：會一併更新韌體",
  update_content: "更新內容：只寫入 GIF 和音樂",
};

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------
function setStatus(text, kind = "") {
  const el = $("status");
  el.textContent = text || "";
  el.className = `status ${kind}`;
  $("rescue").hidden = !(kind === "error" && text && text.includes("BOOT"));
}

window.studio = {
  onProgress(stage, fraction) {
    $("progress").hidden = false;
    $("progress").value = fraction;
    const label = stage === "connect" ? "連線中…" : "燒錄中…";
    setStatus(`${label} ${Math.round(fraction * 100)}%`);
  },
  onFilesDropped(paths) {
    handleFiles(paths);
  },
};

// ---------------------------------------------------------------------------
// LED preview
// ---------------------------------------------------------------------------
// The panel library applies a CIE 1931 lightness curve and dims with brightness;
// approximate what the eye sees, relative to the maximum allowed brightness (128).
function buildLut(brightness) {
  const lut = new Uint8ClampedArray(256);
  for (let c = 0; c < 256; c++) {
    const L = (c / 255) * 100;
    const Y = L <= 8 ? L / 903.3 : Math.pow((L + 16) / 116, 3);
    lut[c] = Math.round(Math.pow(Y * (brightness / 128), 1 / 2.2) * 255);
  }
  return lut;
}

function decode(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function geometry() {
  const stage = $("stage");
  const seam = preview.width > 64 ? 1 : 0;
  const cols = preview.width + seam;  // one empty cell between the two panels
  const cell = Math.max(3, Math.min(14, Math.floor((stage.clientWidth - 16) / cols)));
  return { cell, seamCells: seam, cols };
}

function drawFrame() {
  const canvas = $("led");
  const ctx = canvas.getContext("2d");
  const { cell, cols } = geometry();
  canvas.width = cols * cell;
  canvas.height = preview.height * cell;
  ctx.fillStyle = "#050505";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const frame = preview.frames[preview.index];
  const lut = preview.lut;
  const r = cell * 0.42;
  for (let y = 0; y < preview.height; y++) {
    for (let x = 0; x < preview.width; x++) {
      let R = 0, G = 0, B = 0;
      if (frame) {
        const i = (y * preview.width + x) * 3;
        R = frame.rgb[i]; G = frame.rgb[i + 1]; B = frame.rgb[i + 2];
      }
      const lit = R | G | B;
      ctx.fillStyle = lit ? `rgb(${lut[R]},${lut[G]},${lut[B]})` : "#161616";
      const cx = (x + (x >= 64 ? 1 : 0)) * cell + cell / 2;
      ctx.beginPath();
      ctx.arc(cx, y * cell + cell / 2, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  $("frame-info").textContent = preview.frames.length
    ? `第 ${preview.index + 1} / ${preview.frames.length} 格 · ${preview.frames[preview.index].ms} ms`
    : "—";
}

function schedule() {
  clearTimeout(preview.timer);
  if (!preview.playing || preview.frames.length < 2) return;
  preview.timer = setTimeout(() => {
    preview.index = (preview.index + 1) % preview.frames.length;
    drawFrame();
    schedule();
  }, preview.frames[preview.index].ms);
}

let previewTimer = null;
function requestPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshPreview, 200);
}

async function refreshPreview() {
  preview.lut = buildLut(layout.brightness);
  if (!hasGif) {
    preview.frames = [];
    preview.width = 64 * layout.chain;
    drawFrame();
    return;
  }
  const res = await api().render_preview(placement());
  if (!res.ok) {
    setStatus(res.message, "error");
    return;
  }
  preview.frames = res.frames.map((f) => ({ rgb: decode(f.rgb_b64), ms: f.ms }));
  preview.width = res.width;
  preview.height = res.height;
  preview.index = Math.min(preview.index, preview.frames.length - 1);
  drawFrame();
  schedule();
}

// ---------------------------------------------------------------------------
// Controls
// ---------------------------------------------------------------------------
function syncControls() {
  for (const b of $("chain").children) b.classList.toggle("active", Number(b.dataset.value) === layout.chain);
  for (const b of $("split").children) b.classList.toggle("active", String(layout.split) === b.dataset.value);
  $("split").querySelector('[data-value="true"]').disabled = layout.chain !== 2;
  $("swap").disabled = layout.chain !== 2;
  $("offset").textContent = `X ${layout.offset_x} · Y ${layout.offset_y}`;
  $("scale-val").textContent = `${Math.round(layout.scale * 100)}%`;
  $("brightness-val").textContent = layout.brightness;
  $("flash").disabled = busy;
}

function update(changes) {
  Object.assign(layout, changes);
  if (layout.chain !== 2) { layout.split = false; layout.swap = false; $("swap").checked = false; }
  layout.offset_x = Math.max(-128, Math.min(128, layout.offset_x));
  layout.offset_y = Math.max(-128, Math.min(128, layout.offset_y));
  syncControls();
  requestPreview();
}

function bindControls() {
  for (const b of $("chain").children) b.addEventListener("click", () => setPanelCount(Number(b.dataset.value)));
  for (const b of $("split").children) b.addEventListener("click", () => update({ split: b.dataset.value === "true" }));
  for (const b of document.querySelectorAll(".nudge [data-dx]")) {
    b.addEventListener("click", () => update({
      offset_x: layout.offset_x + Number(b.dataset.dx), offset_y: layout.offset_y + Number(b.dataset.dy),
    }));
  }
  $("reset-pos").addEventListener("click", () => {
    $("scale").value = 100;
    update({ offset_x: 0, offset_y: 0, scale: 1.0 });
  });
  $("scale").addEventListener("input", (e) => update({ scale: Number(e.target.value) / 100 }));
  $("brightness").addEventListener("input", (e) => {
    layout.brightness = Number(e.target.value);
    preview.lut = buildLut(layout.brightness);
    syncControls();
    drawFrame();
  });
  $("swap").addEventListener("change", (e) => update({ swap: e.target.checked }));

  $("play").addEventListener("click", () => {
    preview.playing = !preview.playing;
    $("play").textContent = preview.playing ? "⏸" : "▶︎";
    schedule();
  });
  const step = (d) => {
    if (!preview.frames.length) return;
    preview.playing = false;
    $("play").textContent = "▶︎";
    clearTimeout(preview.timer);
    preview.index = (preview.index + d + preview.frames.length) % preview.frames.length;
    drawFrame();
  };
  $("step-back").addEventListener("click", () => step(-1));
  $("step-fwd").addEventListener("click", () => step(1));

  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || $("confirm").open) return;
    const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const m = moves[e.key];
    if (m && hasGif) {
      e.preventDefault();
      update({ offset_x: layout.offset_x + m[0], offset_y: layout.offset_y + m[1] });
    }
  });

  // Drag the preview to move the picture (in whole LEDs).
  const canvas = $("led");
  let drag = null;
  canvas.addEventListener("mousedown", (e) => {
    if (!hasGif) return;
    drag = { x: e.clientX, y: e.clientY, ox: layout.offset_x, oy: layout.offset_y };
    canvas.classList.add("dragging");
  });
  window.addEventListener("mousemove", (e) => {
    if (!drag) return;
    const scale = canvas.width / canvas.getBoundingClientRect().width;
    const { cell } = geometry();
    const dx = Math.round(((e.clientX - drag.x) * scale) / cell);
    const dy = Math.round(((e.clientY - drag.y) * scale) / cell);
    if (drag.ox + dx !== layout.offset_x || drag.oy + dy !== layout.offset_y) {
      update({ offset_x: drag.ox + dx, offset_y: drag.oy + dy });
    }
  });
  window.addEventListener("mouseup", () => { drag = null; canvas.classList.remove("dragging"); });
  window.addEventListener("resize", drawFrame);
}

// ---------------------------------------------------------------------------
// Files: GIF and music
// ---------------------------------------------------------------------------
async function openGif(path) {
  const res = await api().open_gif(path);
  if (!res.ok) { setStatus(res.message, "error"); return; }
  hasGif = true;
  preview.index = 0;
  $("gif-name").textContent = `${res.name} · ${res.frames} 格`;
  $("drop-hint").hidden = true;
  $("change-gif").hidden = false;
  setStatus("");
  await refreshPreview();
}

function renderMusic(state) {
  if (!state.ok) { setStatus(state.message, "error"); return; }
  if (musicState && state !== musicState) stopAudition(false);  // list changed: indexes moved
  musicState = state;
  const list = $("tracks");
  list.replaceChildren();
  state.tracks.forEach((t, i) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = `${i + 1}. ${t.name}`;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = `${(t.size / 1048576).toFixed(1)} MB · ${t.channels === 1 ? "單聲道" : "立體聲"}`;
    const btn = (label, title, fn) => {
      const b = document.createElement("button");
      b.textContent = label; b.title = title; b.addEventListener("click", fn);
      return b;
    };
    li.append(name, meta,
      btn(auditionIndex === i ? "⏹" : "▶", auditionIndex === i ? "停止" : "試聽", () => audition(i)),
      btn("↑", "往上移", async () => i > 0 && renderMusic(await api().move_track(i, i - 1))),
      btn("↓", "往下移", async () => i < state.tracks.length - 1 && renderMusic(await api().move_track(i, i + 1))),
      btn("✕", "移除", async () => renderMusic(await api().remove_track(i))));
    list.append(li);
  });
  $("tracks-empty").hidden = state.tracks.length > 0;
  const pct = Math.min(100, (state.used / state.capacity) * 100);
  $("capacity-bar").style.width = `${pct}%`;
  $("capacity-bar").classList.toggle("full", pct > 95);
  $("capacity-text").textContent =
    `${(state.used / 1048576).toFixed(1)} MB / ${(state.capacity / 1048576).toFixed(0)} MB`;
  const rej = $("rejected");
  rej.replaceChildren(...(state.rejected || []).map((r) => {
    const li = document.createElement("li");
    li.textContent = `${r.name}：${r.message}`;
    return li;
  }));
}

function stopAudition(redraw = true) {
  const player = $("audition");
  player.pause();
  player.removeAttribute("src");
  auditionIndex = -1;
  if (redraw && musicState) renderMusic(musicState);
}

async function audition(index) {
  if (auditionIndex === index) { stopAudition(); return; }   // second click on the same track = stop
  const player = $("audition");
  const res = await api().track_data_url(index);
  if (!res.ok) { setStatus(res.message, "error"); return; }
  player.src = res.url;
  auditionIndex = index;
  renderMusic(musicState);
  player.onended = () => stopAudition();
  player.play().catch((err) => {
    stopAudition();
    setStatus(`無法試聽：${err.message}`, "error");
  });
}

async function handleFiles(paths) {
  const gifs = paths.filter((p) => p.toLowerCase().endsWith(".gif"));
  const mp3s = paths.filter((p) => p.toLowerCase().endsWith(".mp3"));
  const others = paths.length - gifs.length - mp3s.length;
  if (gifs.length) await openGif(gifs[0]);
  if (mp3s.length) renderMusic(await api().add_tracks(mp3s));
  if (others) setStatus("只支援 GIF 和 MP3 檔案，其他檔案已略過", "error");
  else if (gifs.length > 1) setStatus("一次只能使用一個 GIF，已使用第一個", "error");
}

async function pick(kind) {
  const res = await api().pick_files(kind);
  if (res.ok && res.paths.length) await handleFiles(res.paths);
}

// ---------------------------------------------------------------------------
// Board and flashing
// ---------------------------------------------------------------------------
async function findBoard() {
  const res = await api().find_board();
  const select = $("port");
  select.replaceChildren(...res.ports.map((p) => new Option(p, p)));
  select.hidden = res.ports.length < 2;
  $("board-status").textContent = res.message;
  $("board-status").className = res.ports.length ? "" : "muted";
}

function setBusy(value) {
  busy = value;
  $("find").disabled = value;
  syncControls();
}

function confirmDialog(plan) {
  return new Promise((resolve) => {
    $("confirm-title").textContent = KIND_TEXT[plan.kind] || "確認燒錄";
    $("confirm-notes").replaceChildren(...plan.notes.map((n) => {
      const li = document.createElement("li");
      li.textContent = n;
      return li;
    }));
    const dlg = $("confirm");
    const done = (ok) => { dlg.close(); resolve(ok); };
    $("confirm-ok").onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
    dlg.oncancel = () => resolve(false);
    dlg.showModal();
  });
}

async function flash() {
  setBusy(true);
  $("progress").hidden = true;
  $("progress").value = 0;
  setStatus("讀取板子狀態中…");
  try {
    const port = $("port").value || null;
    const plan = await api().prepare_flash(placement(), port);
    if (!plan.ok) { setStatus(plan.message, "error"); return; }
    setStatus("");
    if (!(await confirmDialog(plan))) {
      await api().cancel_flash();
      setStatus("已取消燒錄");
      return;
    }
    const res = await api().confirm_flash();
    setStatus(res.message, res.ok ? "ok" : "error");
    if (res.ok) $("progress").value = 1;
  } catch (err) {
    setStatus(`發生未預期的錯誤：${err.message}`, "error");
  } finally {
    setBusy(false);
    findBoard();
  }
}

// ---------------------------------------------------------------------------
window.addEventListener("pywebviewready", async () => {
  bindControls();
  $("find").addEventListener("click", findBoard);
  $("pick-gif").addEventListener("click", () => pick("gif"));
  $("change-gif").addEventListener("click", () => pick("gif"));
  $("pick-mp3").addEventListener("click", () => pick("mp3"));
  $("flash").addEventListener("click", flash);
  const arr = await api().get_arrangement();
  if (arr.ok) layout.chain = arr.arrangement.panels.length;
  syncControls();
  refreshPreview();
  findBoard();
});
