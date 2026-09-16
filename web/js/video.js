/* 推流渲染：分块增量 canvas 渲染（合批）+ 冻结检测 + MJPEG 降级保命。 */
import {
  $, screenEl, stage, vcanvas, vctx, ccanvas, cctx, vwrap, setView,
  relay, lastOnline, streamUrl, wsUrl, vmode, vSignal, vLastMsg, vLastFrame,
  setVmode, setVSignal, setVws, touchVMsg, touchVFrame,
} from "./state.js";
import { startMjpeg } from "./mjpeg.js";

export var vframe = { w: 0, h: 0, cols: 0, rows: 0, block: 64 };

function showView(which) {
  if (which === "canvas") {
    vwrap.classList.add("show");
    screenEl.style.display = "none";
    setView(vcanvas);
  } else {
    vwrap.classList.remove("show");
    screenEl.style.display = "";
    setView(screenEl);
  }
}
export function fitCanvas() {
  if (!vframe.w || !vframe.h) return;
  var availW = stage.clientWidth || window.innerWidth;
  var availH = stage.clientHeight || 300;
  var s = Math.min(availW / vframe.w, availH / vframe.h, 1);
  vcanvas.style.width = Math.round(vframe.w * s) + "px";
  vcanvas.style.height = Math.round(vframe.h * s) + "px";
  ccanvas.style.width = vcanvas.style.width;
  ccanvas.style.height = vcanvas.style.height;
}
window.addEventListener("resize", function () { if (vmode === "blocks") fitCanvas(); });

function ensureCanvas(w, h) {
  if (vcanvas.width !== w || vcanvas.height !== h) {
    vcanvas.width = w; vcanvas.height = h;
    ccanvas.width = w; ccanvas.height = h;
    vctx.fillStyle = "#0d0f14";
    vctx.fillRect(0, 0, w, h);
    fitCanvas();
  }
  if (vmode !== "blocks") {
    setVmode("blocks");
    showView("canvas");
  }
}
function drawJpeg(jpeg, x, y) {
  var blob = new Blob([jpeg], { type: "image/jpeg" });
  if (window.createImageBitmap) {
    createImageBitmap(blob).then(function (bmp) {
      vctx.drawImage(bmp, x, y);
    }).catch(function () { imgFallback(blob, x, y); });
  } else imgFallback(blob, x, y);
}

/* ---------- 分块绘制合批：一条消息里的多个块在同一帧内统一提交 ----------
   之前每块独立 createImageBitmap+drawImage：动画区域常覆盖多块，一次 rAF 内
   同一块可能被反复解码重绘。合批后：同位置只画最后一块（覆盖关系不变），
   整批一次 rAF 提交，GPU 合成次数显著下降，弱机不再掉帧。 */
var drawQueue = [], drawScheduled = false;
function queueJpeg(jpeg, x, y) {
  drawQueue.push({ d: jpeg, x: x, y: y });
  if (!drawScheduled) {
    drawScheduled = true;
    requestAnimationFrame(flushDraws);
  }
}
function flushDraws() {
  drawScheduled = false;
  var q = drawQueue; drawQueue = [];
  if (!q.length) return;
  var kept = [];
  for (var i = 0; i < q.length; i++) {
    var it = q[i], dup = -1;
    for (var j = 0; j < kept.length; j++) {
      if (kept[j].x === it.x && kept[j].y === it.y) { dup = j; break; }
    }
    if (dup >= 0) kept[dup] = it; else kept.push(it);
  }
  for (var k = 0; k < kept.length; k++) {
    (function (item) {
      var blob = new Blob([item.d], { type: "image/jpeg" });
      if (window.createImageBitmap) {
        createImageBitmap(blob).then(function (bmp) {
          vctx.drawImage(bmp, item.x, item.y);
        }).catch(function () { imgFallback(blob, item.x, item.y); });
      } else imgFallback(blob, item.x, item.y);
    })(kept[k]);
  }
}
function imgFallback(blob, x, y) {
  var url = URL.createObjectURL(blob);
  var im = new Image();
  im.onload = function () { vctx.drawImage(im, x, y); URL.revokeObjectURL(url); };
  im.onerror = function () { URL.revokeObjectURL(url); };
  im.src = url;
}
function drawCursor(cx, cy) {
  cctx.clearRect(0, 0, ccanvas.width, ccanvas.height);
  cctx.beginPath();
  cctx.moveTo(cx, cy);
  cctx.lineTo(cx + 14, cy + 10);
  cctx.lineTo(cx + 8, cy + 14);
  cctx.lineTo(cx + 5, cy + 19);
  cctx.lineTo(cx + 2, cy + 13);
  cctx.closePath();
  cctx.fillStyle = "#fff";
  cctx.fill();
  cctx.strokeStyle = "#111";
  cctx.lineWidth = 1.5;
  cctx.stroke();
}

var VBLOCK = 64; // 与电脑端 BlockEncoder.BLOCK 一致
function vText(d) {
  if (d.t === "init") {
    vframe.w = d.w; vframe.h = d.h;
    vframe.cols = d.cols; vframe.rows = d.rows; vframe.block = d.block;
  }
}
function vBinary(data) {
  var t = data[0];
  if (t === 1) { // 全帧（长度 4 字节，JPEG 可能超 64KB）
    var w = (data[1] << 8) | data[2];
    var h = (data[3] << 8) | data[4];
    var len = (data[5] << 24) | (data[6] << 16) | (data[7] << 8) | data[8];
    vframe.w = w; vframe.h = h;
    vframe.block = VBLOCK;
    vframe.cols = Math.ceil(w / VBLOCK); // 中继模式无 init 文本，自行推导网格
    vframe.rows = Math.ceil(h / VBLOCK);
    ensureCanvas(w, h);
    drawJpeg(data.subarray(9, 9 + len), 0, 0);
    drawCursor((data[9 + len] << 8) | data[10 + len], (data[11 + len] << 8) | data[12 + len]);
  } else if (t === 2) { // 变化块
    var n = (data[1] << 8) | data[2];
    var p = 3;
    for (var i = 0; i < n; i++) {
      var idx = (data[p] << 8) | data[p + 1];
      var l = (data[p + 2] << 8) | data[p + 3];
      if (vframe.cols) {
        var col = idx % vframe.cols, row = Math.floor(idx / vframe.cols);
        queueJpeg(data.subarray(p + 4, p + 4 + l), col * vframe.block, row * vframe.block);
      }
      p += 4 + l;
    }
    if (p + 4 <= data.length) {
      drawCursor((data[p] << 8) | data[p + 1], (data[p + 2] << 8) | data[p + 3]);
    }
  } else if (t === 4) { // 光标
    drawCursor((data[1] << 8) | data[2], (data[3] << 8) | data[4]);
  }
}

var freezeTimer = null;

function openVStream() {
  if (!window.WebSocket) { startMjpeg(); return; }
  var vws;
  try { vws = new WebSocket(wsUrl("/vstream")); }
  catch (e) { startMjpeg(); return; }
  setVws(vws);
  vws.binaryType = "arraybuffer";
  setVSignal(false);
  vws.onmessage = function (e) {
    touchVMsg();          // 任何消息都算"流还活着"（含心跳）
    setVSignal(true);
    if (typeof e.data === "string") {
      try { vText(JSON.parse(e.data)); } catch (err) {}
    } else {
      vBinary(new Uint8Array(e.data));
      touchVFrame();      // 二进制消息 = 真实画面数据到达
    }
  };
  touchVMsg();
  if (!freezeTimer) {
    freezeTimer = setInterval(function () {
      var now = Date.now();
      var vmodeNow = vmode, vwsNow = vws;
      // 检测A：12 秒无任何消息（心跳 5 秒一次）= 传输死 → 断开由 onclose 重连
      if (vmodeNow === "blocks" && vwsNow && now - vLastMsg > 12000) {
        try { vwsNow.close(); } catch (err) {}
        return;
      }
      // 检测B：链路活着（心跳在跳）但 30 秒无画面帧 = 采集卡住（锁屏/驱动挂起）
      // → 重连无法解决，显示提示条，采集恢复后自动消失
      var frozen = $("frozen");
      if (frozen) {
        var bad = vmodeNow === "blocks" && vwsNow && vLastFrame && now - vLastFrame > 30000;
        frozen.hidden = !bad;
      }
    }, 3000);
  }
  vws.onclose = function () {
    var wasBlocks = vmode === "blocks";
    setVws(null);
    if (wasBlocks) setTimeout(openVStream, 1500); // 断线静默重连，保留已渲染画面
    else if (vmode === "none") startMjpeg();
  };
  vws.onerror = function () { try { vws.close(); } catch (e) {} };
}
export function startVideo() {
  if (vmode === "blocks") return;
  if (relay && lastOnline === false) return; // 设备离线，等上线再连
  setVmode("none");
  setVSignal(false);
  openVStream();
  setTimeout(function () {
    // 3 秒没收到任何帧/活信号 → 降级 MJPEG（心跳/init 文本也算活信号，
    // 静止桌面上分块流健康但无画面变化，不能误降级）
    if (vmode === "none" && !vSignal) {
      try { vws.close(); } catch (e) {}
      startMjpeg();
    }
  }, 3000);
}
