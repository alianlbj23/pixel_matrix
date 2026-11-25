"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

const PANEL_W = 64;
const PANEL_H = 32;
const MAX_PANELS = 8;
const ROTATIONS = [0, 90, 180, 270];
const ARROWS = { 0: "↑", 90: "→", 180: "↓", 270: "←" };   // points at the panel's "up" edge (ly = 0)

// GIF placement on the arrangement canvas (sent to render_preview / prepare_flash).
const layout = { split: false, offset_x: 0, offset_y: 0, scale: 1.0, brightness: 90 };
// Panel arrangement as last returned by the backend.
const arrangement = { panels: [{ index: 0, x: 0, y: 0, rotation: 0 }], grid: true, max_frames: 64, power_warning: false };
const preview = { frames: [], width: PANEL_W, height: PANEL_H, panels: [], index: 0, playing: true, timer: null, lut: null };
let hasGif = false;
let auditionIndex = -1;   // track being auditioned, -1 when stopped
let musicState = null;
let arrangeBusy = false;  // an arrangement call is in flight: ignore further edits until it answers
let identifyBusy = false;

// Flashing: null when idle, otherwise "prepare" (reading the board / confirm dialog) or "write" (confirm_flash).
const flashState = { phase: null, stoppable: false, stopping: false };
const isFlashing = () => flashState.phase !== null;

// App update: the prompt is shown at most once per session and deferred while flashing.
// updating: start_update is running (download + install); every .lockable control is locked meanwhile.
const updateState = { updating: false, prompted: false, pending: false, cancelling: false };
const MSG_UPDATING = "正在更新程式，請稍候";
const MSG_FLASH_LOCKED = "燒錄中，請先停止燒錄";
const isLocked = () => isFlashing() || updateState.updating;
const lockMessage = () => (updateState.updating ? MSG_UPDATING : MSG_FLASH_LOCKED);

function placement() {
  return {
    mode: layout.split ? "split" : "whole", offset_x: layout.offset_x, offset_y: layout.offset_y,
    scale: layout.scale, brightness: layout.brightness,
  };
}

const KIND_TEXT = {
  first_install: "首次安裝：會寫入完整韌體、GIF 和音樂",
  update_firmware: "更新韌體：會一併更新韌體",
  update_content: "更新內容：只寫入 GIF 和音樂",
};

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------
let statusSource = "";   // who wrote the current status line ("preview" errors clear themselves)

function setStatus(text, kind = "", source = "") {
  const el = $("status");
  el.textContent = text || "";
  el.className = `status ${kind}`;
  statusSource = text ? source : "";
  $("rescue").hidden = !(kind === "error" && text && text.includes("BOOT"));
}

function reportError(context, err) {
  console.error(context, err);
  setStatus(`發生未預期的錯誤：${err && err.message ? err.message : err}`, "error");
}

window.studio = {
  onProgress(stage, fraction, stoppable) {
    if (!isFlashing()) return;   // a late callback after the flash already ended
    const f = Number.isFinite(fraction) ? Math.max(0, Math.min(1, fraction)) : 0;
    flashState.stoppable = stoppable === true;
    $("progress").hidden = false;
    $("progress").value = f;
    const label = stage === "connect" ? "連線中…" : "燒錄中…";
    setStatus(`${label} ${Math.round(f * 100)}%`);
    syncStop();
  },
  onCloseBlocked(message) {
    setStatus(typeof message === "string" && message ? message : lockMessage(), "error");
  },
  onUpdateProgress(fraction) {
    if (!updateState.updating) return;   // a late callback after start_update already answered
    const f = Number.isFinite(fraction) ? Math.max(0, Math.min(1, fraction)) : 0;
    $("update-progress").value = f;
    if (f >= 1) {
      $("update-cancel").hidden = true;   // installing next: no longer cancellable
      $("update-status").textContent = "下載完成，正在安裝新版本…";
    } else if (!updateState.cancelling) {
      $("update-status").textContent = `下載新版本中… ${Math.round(f * 100)}%`;
    }
  },
  onFilesDropped(paths) {
    if (isLocked()) { setStatus(lockMessage(), "error"); return; }
    if (!Array.isArray(paths)) return;
    handleFiles(paths.filter((p) => typeof p === "string")).catch((err) => reportError("drop", err));
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

const MAX_STAGE_H = 440;   // CSS px the preview may grow to before the cells shrink

function geometry() {
  const stage = $("stage");
  const fitW = Math.floor((stage.clientWidth - 16) / preview.width);
  const fitH = Math.floor(MAX_STAGE_H / preview.height);
  return { cell: Math.max(1, Math.min(14, fitW, fitH)) };
}

function drawFrame() {
  const canvas = $("led");
  const ctx = canvas.getContext("2d");
  const { cell } = geometry();
  canvas.width = preview.width * cell;
  canvas.height = preview.height * cell;
  ctx.fillStyle = "#050505";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const frame = preview.frames[preview.index];
  const lut = preview.lut || buildLut(layout.brightness);
  const dots = cell >= 3;   // tiny cells: plain squares (1 px minimum) so very big canvases stay readable
  const r = cell * 0.42;
  for (const p of preview.panels) {
    const x1 = Math.min(preview.width, p.x + p.w);
    const y1 = Math.min(preview.height, p.y + p.h);
    for (let y = Math.max(0, p.y); y < y1; y++) {
      for (let x = Math.max(0, p.x); x < x1; x++) {
        let R = 0, G = 0, B = 0;
        if (frame) {
          const i = (y * preview.width + x) * 3;
          R = frame.rgb[i]; G = frame.rgb[i + 1]; B = frame.rgb[i + 2];
        }
        ctx.fillStyle = (R | G | B) ? `rgb(${lut[R]},${lut[G]},${lut[B]})` : "#161616";
        if (dots) {
          ctx.beginPath();
          ctx.arc(x * cell + cell / 2, y * cell + cell / 2, r, 0, Math.PI * 2);
          ctx.fill();
        } else {
          ctx.fillRect(x * cell, y * cell, cell, cell);
        }
      }
    }
  }
  ctx.strokeStyle = "#3d4552";
  ctx.lineWidth = 1;
  for (const p of preview.panels) ctx.strokeRect(p.x * cell + 0.5, p.y * cell + 0.5, p.w * cell - 1, p.h * cell - 1);
  $("frame-info").textContent = frame
    ? `第 ${preview.index + 1} / ${preview.frames.length} 格 · ${frame.ms} ms`
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

// Empty panels at their arrangement positions (no GIF yet, or the GIF cannot be rendered).
function showEmptyPanels() {
  clearTimeout(preview.timer);
  const rects = panelRects(arrangement.panels);
  preview.frames = [];
  preview.panels = rects;
  preview.width = Math.max(...rects.map((p) => p.x + p.w));
  preview.height = Math.max(...rects.map((p) => p.y + p.h));
  preview.index = 0;
  drawFrame();
}

function validPreview(res) {
  const okInt = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;
  return okInt(res.width, 1, 1024) && okInt(res.height, 1, 1024)
    && Array.isArray(res.panels) && res.panels.length >= 1
    && res.panels.every((p) => p && okInt(p.x, 0, 1024) && okInt(p.y, 0, 1024) && okInt(p.w, 1, 64) && okInt(p.h, 1, 64))
    && Array.isArray(res.frames)
    && res.frames.every((f) => f && typeof f.rgb_b64 === "string" && Number.isFinite(f.ms));
}

let previewTimer = null;
let previewSeq = 0;
function requestPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => refreshPreview().catch((err) => reportError("preview", err)), 200);
}

async function refreshPreview() {
  preview.lut = buildLut(layout.brightness);
  if (!hasGif) { showEmptyPanels(); return; }
  if (isLocked()) return;   // render_preview is refused while flashing/updating; redraw after unlock
  const seq = ++previewSeq;
  const res = await api().render_preview(placement());
  if (seq !== previewSeq) return;   // a newer request is on its way
  if (!res.ok && isLocked()) return;   // refused because flashing/updating started meanwhile; redrawn after unlock
  if (!res.ok || !validPreview(res)) {
    setStatus(res.ok ? "預覽資料不正確，請重新加入 GIF" : res.message, "error", "preview");
    showEmptyPanels();
    return;
  }
  const size = res.width * res.height * 3;
  const frames = res.frames.map((f) => ({ rgb: decode(f.rgb_b64), ms: f.ms }));
  if (frames.some((f) => f.rgb.length !== size)) {
    setStatus("預覽資料不正確，請重新加入 GIF", "error", "preview");
    showEmptyPanels();
    return;
  }
  if (statusSource === "preview") setStatus("");
  preview.frames = frames;
  preview.panels = res.panels;
  preview.width = res.width;
  preview.height = res.height;
  preview.index = Math.max(0, Math.min(preview.index, frames.length - 1));
  drawFrame();
  schedule();
}

// ---------------------------------------------------------------------------
// Panel arrangement editor
// ---------------------------------------------------------------------------
const EDIT_MARGIN = 64;      // LED px of free space around the panels so a tile can be dropped beside them
const EDIT_MAX_H = 360;      // CSS px
const EDIT_MAX_SCALE = 3;    // CSS px per LED
const DRAG_THRESHOLD = 4;    // CSS px of mouse movement before a press counts as a drag (else: rotate)
const editor = { scale: 1, drag: null };

const panelSize = (rotation) => (rotation === 90 || rotation === 270 ? [PANEL_H, PANEL_W] : [PANEL_W, PANEL_H]);

function panelRects(panels) {
  return panels.map((p) => {
    const [w, h] = panelSize(p.rotation);
    return { index: p.index, x: p.x, y: p.y, w, h, rotation: p.rotation };
  });
}

// Validates an arrangement state from the backend; returns false (and keeps the old one) when malformed.
function acceptArrangement(res) {
  const a = res && res.arrangement;
  const okInt = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;
  if (!a || !Array.isArray(a.panels) || a.panels.length < 1 || a.panels.length > MAX_PANELS
      || !a.panels.every((p, i) => p && p.index === i && okInt(p.x, 0, 1024) && okInt(p.y, 0, 1024)
        && ROTATIONS.includes(p.rotation))
      || typeof res.grid !== "boolean" || !okInt(res.max_frames, 1, 1000)) {
    console.error("bad arrangement state", res);
    return false;
  }
  arrangement.panels = a.panels.map((p) => ({ index: p.index, x: p.x, y: p.y, rotation: p.rotation }));
  arrangement.grid = res.grid;
  arrangement.max_frames = res.max_frames;
  arrangement.power_warning = res.power_warning === true;
  if (!arrangement.grid) layout.split = false;   // "每塊一份" only exists for grid arrangements
  return true;
}

function drawArrangement() {
  const area = $("arrange-area");
  const rects = panelRects(arrangement.panels);
  const W = Math.max(...rects.map((p) => p.x + p.w)) + 2 * EDIT_MARGIN;
  const H = Math.max(...rects.map((p) => p.y + p.h)) + 2 * EDIT_MARGIN;
  const avail = Math.max(120, area.parentElement.clientWidth - 40);
  const s = Math.min(EDIT_MAX_SCALE, avail / W, EDIT_MAX_H / H);
  editor.scale = s;
  area.style.width = `${Math.round(W * s)}px`;
  area.style.height = `${Math.round(H * s)}px`;
  area.replaceChildren(...rects.map((p) => makeTile(p, s)));
  const n = rects.length;
  $("panel-count").textContent = `${n} 塊`;
  $("arrange-info").textContent = `GIF 最多 ${arrangement.max_frames} 格`;
  $("power-warning").hidden = !arrangement.power_warning;
}

function makeTile(p, s) {
  const tile = document.createElement("div");
  tile.className = "tile";
  tile.dataset.index = p.index;
  placeTile(tile, p.x, p.y, p.w, p.h, s);
  tile.title = `第 ${p.index + 1} 塊（拖曳移動，點一下旋轉）`;
  const side = Math.min(p.w, p.h) * s;
  const num = document.createElement("span");
  num.className = "num";
  num.textContent = String(p.index + 1);
  num.style.fontSize = `${Math.max(10, Math.round(side * 0.6))}px`;
  const arrow = document.createElement("span");
  arrow.className = `arrow up-${p.rotation}`;
  arrow.textContent = ARROWS[p.rotation];
  arrow.style.fontSize = `${Math.max(9, Math.round(side * 0.32))}px`;
  tile.append(num, arrow);
  tile.addEventListener("pointerdown", (e) => startTileDrag(e, tile, p));
  return tile;
}

function placeTile(tile, x, y, w, h, s) {
  tile.style.left = `${(x + EDIT_MARGIN) * s}px`;
  tile.style.top = `${(y + EDIT_MARGIN) * s}px`;
  tile.style.width = `${w * s}px`;
  tile.style.height = `${h * s}px`;
}

function startTileDrag(e, tile, p) {
  if (e.button !== 0 || isLocked() || arrangeBusy || editor.drag) return;
  e.preventDefault();
  tile.setPointerCapture(e.pointerId);
  editor.drag = { tile, p, startX: e.clientX, startY: e.clientY, x: p.x, y: p.y, moved: false, ghost: null };
  tile.addEventListener("pointermove", onTileMove);
  tile.addEventListener("pointerup", onTileUp);
  tile.addEventListener("pointercancel", onTileCancel);
}

function onTileMove(e) {
  const d = editor.drag;
  if (!d) return;
  const dx = e.clientX - d.startX;
  const dy = e.clientY - d.startY;
  if (!d.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
  if (!d.moved) {
    d.moved = true;
    d.ghost = d.tile.cloneNode(true);
    d.ghost.classList.add("ghost");
    d.tile.classList.add("dragging");
    $("arrange-area").append(d.ghost);
  }
  d.x = Math.round(d.p.x + dx / editor.scale);
  d.y = Math.round(d.p.y + dy / editor.scale);
  placeTile(d.ghost, d.x, d.y, d.p.w, d.p.h, editor.scale);
}

function endTileDrag() {
  const d = editor.drag;
  editor.drag = null;
  if (!d) return null;
  d.tile.removeEventListener("pointermove", onTileMove);
  d.tile.removeEventListener("pointerup", onTileUp);
  d.tile.removeEventListener("pointercancel", onTileCancel);
  return d;
}

function onTileUp() {
  const d = endTileDrag();
  if (!d) return;
  if (!d.moved) {
    arrangeCall(() => api().rotate_panel(d.p.index));
  } else if (d.x === d.p.x && d.y === d.p.y) {
    drawArrangement();   // dropped where it started
  } else {
    arrangeCall(() => api().move_panel(d.p.index, d.x, d.y));   // the backend snaps or puts it back
  }
}

function onTileCancel() {
  if (endTileDrag()) drawArrangement();
}

// Runs one arrangement-changing API call and redraws from whatever arrangement it returns.
async function arrangeCall(call) {
  if (arrangeBusy || isLocked()) return;
  arrangeBusy = true;
  try {
    const res = await call();
    if (res && res.arrangement && !acceptArrangement(res)) {
      setStatus("排列資料不正確，請重新開啟程式", "error");
    } else if (res && res.message) {
      setStatus(res.message, "error");   // overlap put back, canvas too big, save failed, locked …
    } else if (res && res.ok && statusSource !== "preview") {
      setStatus("");
    }
  } catch (err) {
    reportError("arrangement", err);
  } finally {
    arrangeBusy = false;
    drawArrangement();
    syncControls();
    requestPreview();
  }
}

async function identifyPanels() {
  if (identifyBusy || isLocked()) return;
  identifyBusy = true;
  syncControls();
  setStatus("正在請板子顯示面板編號…");
  try {
    const res = await api().identify($("port").value || null);
    if (res.ok) {
      setStatus(res.message, "ok");
    } else {
      setStatus(res.message, "error");
      if (res.needs_firmware === true) pointAt($("install-fw"));
    }
  } catch (err) {
    reportError("identify", err);
  } finally {
    identifyBusy = false;
    syncControls();
  }
}

function pointAt(button) {
  button.classList.remove("attention");
  void button.offsetWidth;   // restart the animation
  button.classList.add("attention");
  button.scrollIntoView({ block: "nearest" });
  setTimeout(() => button.classList.remove("attention"), 4000);
}

function bindArrangement() {
  $("panel-minus").addEventListener("click", () =>
    arrangeCall(() => api().set_panel_count(arrangement.panels.length - 1)));
  $("panel-plus").addEventListener("click", () =>
    arrangeCall(() => api().set_panel_count(arrangement.panels.length + 1)));
  for (const b of $("presets").children) {
    b.addEventListener("click", () => arrangeCall(() => api().apply_preset(b.dataset.preset)));
  }
  $("identify").addEventListener("click", identifyPanels);
  $("install-fw").addEventListener("click", () => runFlash(true));
}

// ---------------------------------------------------------------------------
// Controls and the flashing lock
// ---------------------------------------------------------------------------
function syncControls() {
  const locked = isLocked();
  document.body.classList.toggle("flashing", isFlashing());
  document.body.classList.toggle("updating", updateState.updating);
  for (const el of document.querySelectorAll(".lockable")) {
    const controls = el.matches("button, input, select") ? [el] : el.querySelectorAll("button, input, select");
    for (const c of controls) c.disabled = locked;
  }
  for (const b of $("split").children) b.classList.toggle("active", String(layout.split) === b.dataset.value);
  $("split-help").hidden = !arrangement.grid;
  $("split-off").hidden = arrangement.grid;
  $("offset").textContent = `X ${layout.offset_x} · Y ${layout.offset_y}`;
  $("scale-val").textContent = `${Math.round(layout.scale * 100)}%`;
  $("brightness-val").textContent = layout.brightness;
  if (!locked) {
    const n = arrangement.panels.length;
    $("split").querySelector('[data-value="true"]').disabled = !arrangement.grid;
    $("panel-minus").disabled = n <= 1;
    $("panel-plus").disabled = n >= MAX_PANELS;
    $("identify").disabled = identifyBusy;
    $("install-fw").disabled = identifyBusy;   // identify holds the port for up to 1.5 s
  }
  $("flash").disabled = locked || identifyBusy;
  syncStop();
}

function syncStop() {
  const btn = $("stop");
  const note = $("stop-note");
  btn.hidden = !isFlashing();
  if (!isFlashing()) { note.hidden = true; return; }
  // Before confirm_flash nothing is being written: the confirm dialog's 取消 is the way out.
  const canStop = flashState.phase === "write" && flashState.stoppable && !flashState.stopping;
  btn.disabled = !canStop;
  btn.textContent = flashState.stopping ? "停止中…" : "停止燒錄";
  note.hidden = canStop || flashState.stopping;
  note.textContent = flashState.phase === "write" ? "正在寫入韌體，請稍候" : "正在讀取板子狀態，請稍候";
}

function update(changes) {
  Object.assign(layout, changes);
  if (!arrangement.grid) layout.split = false;
  layout.offset_x = Math.max(-128, Math.min(128, layout.offset_x));
  layout.offset_y = Math.max(-128, Math.min(128, layout.offset_y));
  syncControls();
  requestPreview();
}

function bindControls() {
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
    if (e.target.tagName === "INPUT" || $("confirm").open || $("update-dialog").open || isLocked()) return;
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
    if (!hasGif || isLocked()) return;
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
  window.addEventListener("resize", () => { drawFrame(); if (!editor.drag) drawArrangement(); });
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
      b.textContent = label; b.title = title;
      b.addEventListener("click", () => fn().catch((err) => reportError("music", err)));
      return b;
    };
    li.append(name, meta,
      btn(auditionIndex === i ? "⏹" : "▶", auditionIndex === i ? "停止" : "試聽", async () => audition(i)),
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
  syncControls();   // new track buttons pick up the lock
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
  if (isLocked()) { setStatus(lockMessage(), "error"); return; }
  const gifs = paths.filter((p) => p.toLowerCase().endsWith(".gif"));
  const mp3s = paths.filter((p) => p.toLowerCase().endsWith(".mp3"));
  const others = paths.length - gifs.length - mp3s.length;
  if (gifs.length) await openGif(gifs[0]);
  if (mp3s.length) renderMusic(await api().add_tracks(mp3s));
  if (others) setStatus("只支援 GIF 和 MP3 檔案，其他檔案已略過", "error");
  else if (gifs.length > 1) setStatus("一次只能使用一個 GIF，已使用第一個", "error");
}

async function pick(kind) {
  try {
    const res = await api().pick_files(kind);
    if (res.ok && Array.isArray(res.paths) && res.paths.length) await handleFiles(res.paths);
    else if (!res.ok) setStatus(res.message, "error");
  } catch (err) {
    reportError("pick", err);
  }
}

// ---------------------------------------------------------------------------
// Board and flashing
// ---------------------------------------------------------------------------
async function findBoard() {
  try {
    const res = await api().find_board();
    const ports = Array.isArray(res.ports) ? res.ports.filter((p) => typeof p === "string") : [];
    const select = $("port");
    select.replaceChildren(...ports.map((p) => new Option(p, p)));
    select.hidden = ports.length < 2;
    $("board-status").textContent = res.message;
    $("board-status").className = ports.length ? "" : "muted";
  } catch (err) {
    reportError("find_board", err);
  }
}

function setFlashPhase(phase) {
  flashState.phase = phase;
  flashState.stoppable = false;
  flashState.stopping = false;
  syncControls();
}

// Title of the 安裝/更新韌體 confirm dialog; only promises the GIF and music stay when the plan says so.
function firmwareTitle(plan) {
  if (plan.kind === "first_install") return "首次安裝：寫入完整韌體";
  if (plan.music_may_be_lost === true) return "安裝/更新韌體：只寫入韌體，但板子上原本的音樂可能無法播放";
  return "安裝/更新韌體：只寫入韌體，不會改動板子上的 GIF 和音樂";
}

function confirmDialog(plan, firmwareOnly) {
  return new Promise((resolve) => {
    $("confirm-title").textContent = firmwareOnly ? firmwareTitle(plan) : KIND_TEXT[plan.kind] || "確認燒錄";
    const notes = Array.isArray(plan.notes) ? plan.notes.filter((n) => typeof n === "string") : [];
    $("confirm-notes").replaceChildren(...notes.map((n) => {
      const li = document.createElement("li");
      li.textContent = n;
      return li;
    }));
    const dlg = $("confirm");
    const done = (ok) => { if (dlg.open) dlg.close(); resolve(ok); };
    $("confirm-ok").onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
    dlg.oncancel = () => resolve(false);   // Esc closes the dialog by itself
    dlg.showModal();
  });
}

// Content flash (firmwareOnly = false) or the 安裝/更新韌體 button (firmwareOnly = true).
// Editing is locked from the first call until every exit path below has run.
async function runFlash(firmwareOnly) {
  if (isLocked()) return;
  if (auditionIndex >= 0) stopAudition();
  setFlashPhase("prepare");
  $("progress").hidden = true;
  $("progress").value = 0;
  setStatus("讀取板子狀態中…");
  let writing = false;   // confirm_flash was called: never cancel_flash from here on (it would block)
  try {
    const port = $("port").value || null;
    const plan = await api().prepare_flash(firmwareOnly ? null : placement(), port, firmwareOnly);
    if (!plan.ok) {
      setStatus(plan.message, plan.up_to_date ? "ok" : "error");
      return;
    }
    setStatus("");
    if (!(await confirmDialog(plan, firmwareOnly))) {
      setStatus("已取消燒錄");
      return;
    }
    writing = true;
    setFlashPhase("write");
    setStatus("燒錄中…");
    const res = await api().confirm_flash();
    setStatus(res.message, res.ok ? "ok" : "error");
    if (res.ok) $("progress").value = 1;
  } catch (err) {
    reportError("flash", err);
  } finally {
    if (!writing) {
      // Dialog dismissed, prepare failed or something threw before writing: release the backend lock
      // (harmless when prepare_flash already released it).
      try {
        await api().cancel_flash();
      } catch (err) {
        reportError("cancel_flash", err);
      }
    }
    setFlashPhase(null);
    requestPreview();
    findBoard();
    offerDeferredUpdate();   // an update found while flashing is offered now (success, failure or stop)
  }
}

async function stopFlash() {
  if (flashState.phase !== "write" || !flashState.stoppable || flashState.stopping) return;
  flashState.stopping = true;
  syncStop();
  setStatus("正在停止燒錄…");
  try {
    await api().stop_flash();   // never blocks; confirm_flash returns {stopped: true} shortly after
  } catch (err) {
    reportError("stop_flash", err);
  }
}

// ---------------------------------------------------------------------------
// App update
// ---------------------------------------------------------------------------
const UPDATE_POLL_MS = 5000;
const UPDATE_POLL_MAX = 24;   // keep asking for 2 more minutes when the check is still running

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function validUpdateState(res) {
  return res && res.ok === true && typeof res.available === "boolean" && typeof res.version === "string"
    && typeof res.current === "string" && typeof res.has_work === "boolean";
}

// Runs once after start: check failures are only logged, never shown.
async function checkForUpdate() {
  try {
    let res = await api().check_update();
    for (let i = 0; res && res.checking === true && i < UPDATE_POLL_MAX; i++) {
      await sleep(UPDATE_POLL_MS);
      res = await api().get_update_state();
    }
    offerUpdate(res);
  } catch (err) {
    console.error("check_update", err);
  }
}

async function offerDeferredUpdate() {
  if (!updateState.pending) return;
  try {
    offerUpdate(await api().get_update_state());   // fresh has_work: GIF/music may have changed meanwhile
  } catch (err) {
    console.error("get_update_state", err);
  }
}

function offerUpdate(res) {
  if (!validUpdateState(res)) {
    if (res) console.error("bad update state", res);
    return;
  }
  if (!res.available || updateState.prompted || updateState.updating) return;
  if (isFlashing() || res.flashing === true) { updateState.pending = true; return; }
  updateState.pending = false;
  updateState.prompted = true;
  $("update-text").textContent = `目前有新版本 ${res.version}（你的版本 ${res.current}），是否更新？`;
  $("update-work").hidden = !res.has_work;
  const dlg = $("update-dialog");
  const done = (ok) => {
    if (dlg.open) dlg.close();
    if (ok) runUpdate().catch((err) => reportError("update", err));
  };
  $("update-ok").onclick = () => done(true);
  $("update-later").onclick = () => done(false);
  dlg.oncancel = () => done(false);   // Esc = 稍後
  dlg.showModal();
}

async function runUpdate() {
  if (isLocked()) { setStatus(lockMessage(), "error"); return; }
  if (auditionIndex >= 0) stopAudition();
  updateState.updating = true;
  updateState.cancelling = false;
  $("update-progress").value = 0;
  $("update-status").textContent = "下載新版本中…";
  $("update-cancel").hidden = false;
  $("update-cancel").disabled = false;
  $("update-bar").hidden = false;
  syncControls();
  setStatus("");
  let res = null;
  try {
    res = await api().start_update();   // on success the backend closes this window
  } catch (err) {
    reportError("start_update", err);
  }
  if (res && res.ok) {
    $("update-status").textContent = typeof res.message === "string" ? res.message : "正在開啟新版本…";
    $("update-progress").value = 1;
    $("update-cancel").hidden = true;
    return;   // stays locked until the window closes
  }
  updateState.updating = false;
  updateState.cancelling = false;
  $("update-bar").hidden = true;
  syncControls();
  requestPreview();
  if (res) setStatus(res.message, res.installed === true ? "ok" : res.cancelled === true ? "" : "error");
}

// 取消 in the update bar: stops the download; start_update() then answers {cancelled: true}.
async function cancelUpdate() {
  if (!updateState.updating || updateState.cancelling) return;
  updateState.cancelling = true;
  $("update-cancel").disabled = true;
  $("update-status").textContent = "正在取消更新…";
  let res = null;
  try {
    res = await api().cancel_update();
  } catch (err) {
    reportError("cancel_update", err);
  }
  if (res && res.ok) return;
  updateState.cancelling = false;   // too late (installing) or failed: the update goes on
  $("update-cancel").hidden = true;
  if (res && typeof res.message === "string") $("update-status").textContent = res.message;
}

// ---------------------------------------------------------------------------
window.addEventListener("pywebviewready", async () => {
  bindControls();
  bindArrangement();
  $("find").addEventListener("click", findBoard);
  $("pick-gif").addEventListener("click", () => pick("gif"));
  $("change-gif").addEventListener("click", () => pick("gif"));
  $("pick-mp3").addEventListener("click", () => pick("mp3"));
  $("flash").addEventListener("click", () => runFlash(false));
  $("stop").addEventListener("click", stopFlash);
  $("update-cancel").addEventListener("click", cancelUpdate);
  try {
    const arr = await api().get_arrangement();
    if (!acceptArrangement(arr)) setStatus("無法讀取面板排列，先用 1 塊面板", "error");
  } catch (err) {
    reportError("get_arrangement", err);
  }
  drawArrangement();
  syncControls();
  refreshPreview().catch((err) => reportError("preview", err));
  findBoard();
  checkForUpdate();
});
