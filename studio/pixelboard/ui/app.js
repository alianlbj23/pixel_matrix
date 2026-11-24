"use strict";

const $ = (id) => document.getElementById(id);
const STAGE_TEXT = { connect: "連線中…", write: "燒錄中…" };

window.studio = {
  onProgress(stage, fraction) {
    const bar = $("progress");
    bar.hidden = false;
    bar.value = fraction;
    $("status").textContent = `${STAGE_TEXT[stage] || "處理中…"} ${Math.round(fraction * 100)}%`;
  },
};

function setBusy(busy) {
  $("find").disabled = busy;
  $("flash").disabled = busy || $("port").options.length === 0;
}

async function findBoard() {
  setBusy(true);
  try {
    const res = await window.pywebview.api.find_board();
    const select = $("port");
    select.replaceChildren(...res.ports.map((p) => new Option(p, p)));
    select.hidden = res.ports.length < 2;
    $("status").textContent = res.message;
  } catch (err) {
    $("status").textContent = `發生未預期的錯誤：${err.message}`;
  } finally {
    setBusy(false);
  }
}

async function flashFirmware() {
  setBusy(true);
  $("progress").value = 0;
  try {
    const res = await window.pywebview.api.flash_firmware($("port").value || null);
    $("status").textContent = res.message;
    $("status").classList.toggle("error", !res.ok);
  } catch (err) {
    $("status").textContent = `發生未預期的錯誤：${err.message}`;
  } finally {
    setBusy(false);
  }
}

window.addEventListener("pywebviewready", () => {
  $("find").addEventListener("click", findBoard);
  $("flash").addEventListener("click", flashFirmware);
  findBoard();
});
