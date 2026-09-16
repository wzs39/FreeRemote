/* 手势状态机（stage 触摸）：单击=左键 双击=双击 长按=右键 拖动=移动 双指=滚轮/捏合缩放。 */
import {
  $, stage, view, wsOk, info, activeMods, setLastFrame,
} from "./state.js";

var pointers = new Map();
var pressTimer = null, moved = false, multi = false, lastCentroid = null;
var lastSent = null, lastTap = null;

/* ---------- 捏合缩放（本地视图变换，不影响推流） ---------- */
var zoom = { s: 1, x: 0, y: 0 };
var pinch = null;          // { dist, cx, cy, s, x, y } 捏合起始状态
var panning = false;       // 缩放后的单指平移
var panStart = null;       // { px, py, x, y }
var ZOOM_MIN = 1, ZOOM_MAX = 5;
var zoombox = $("zoombox"), zoomhint = $("zoomhint");

function applyZoom() {
  zoombox.style.transform = "translate(" + zoom.x + "px," + zoom.y + "px) scale(" + zoom.s + ")";
  if (zoomhint) {
    var on = zoom.s > 1.02;
    zoomhint.hidden = !on;
    zoomhint.classList.toggle("show", on);
  }
}
export function resetZoom() {
  zoom.s = 1; zoom.x = 0; zoom.y = 0;
  applyZoom();
}
function clampZoom() {
  zoom.s = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoom.s));
  if (zoom.s > 1) {
    var r = view.getBoundingClientRect();
    var vw = window.innerWidth, vh = window.innerHeight;
    var halfW = r.width * zoom.s / 2, halfH = r.height * zoom.s / 2;
    var maxX = Math.max(0, halfW - vw / 2), maxY = Math.max(0, halfH - vh / 2);
    zoom.x = Math.max(-maxX, Math.min(maxX, zoom.x));
    zoom.y = Math.max(-maxY, Math.min(maxY, zoom.y));
  } else { zoom.x = 0; zoom.y = 0; }
}
function pinchState() {
  var pts = Array.from(pointers.values());
  var dx = pts[0].x - pts[1].x, dy = pts[0].y - pts[1].y;
  return { dist: Math.hypot(dx, dy) || 1, cx: (pts[0].x + pts[1].x) / 2, cy: (pts[0].y + pts[1].y) / 2 };
}
function centroid() {
  var xs = 0, ys = 0, n = 0;
  pointers.forEach(function (p) { xs += p.x; ys += p.y; n++; });
  return { x: xs / n, y: ys / n };
}

function clientToFrame(cx, cy) {
  var r = view.getBoundingClientRect();
  if (!r.width || !r.height) return { x: 0, y: 0 };
  return {
    x: (cx - r.left) / r.width * info.w,
    y: (cy - r.top) / r.height * info.h
  };
}

stage.addEventListener("pointerdown", function (e) {
  if (!wsOk || e.button > 0) return;
  e.preventDefault();
  try { stage.setPointerCapture(e.pointerId); } catch (err) {}
  if (pointers.size === 0) { moved = false; multi = false; lastSent = null; }
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, t: Date.now() });
  if (pointers.size === 2) {
    multi = true;
    clearTimeout(pressTimer);
    lastCentroid = centroid();
    var st = pinchState();
    pinch = { dist: st.dist, cx: st.cx, cy: st.cy, s: zoom.s, x: zoom.x, y: zoom.y };
  } else if (pointers.size === 1 && zoom.s > 1.02) {
    panning = true; multi = true;
    panStart = { px: e.clientX, py: e.clientY, x: zoom.x, y: zoom.y };
  } else if (pointers.size === 1 && !multi) {
    clearTimeout(pressTimer);
    pressTimer = setTimeout(function () {
      var p = pointers.get(e.pointerId);
      if (p && !moved && pointers.size === 1) {
        var f = clientToFrame(p.x, p.y);
        setLastFrame(f.x, f.y);
        send({ t: "click", button: "right", x: f.x, y: f.y, mods: activeMods() });
        pointers.delete(e.pointerId);
      }
    }, 500);
  }
});

stage.addEventListener("pointermove", function (e) {
  if (!pointers.has(e.pointerId)) return;
  var p = pointers.get(e.pointerId);
  var dx = e.clientX - p.x, dy = e.clientY - p.y;
  p.x = e.clientX; p.y = e.clientY;
  if (pinch && pointers.size === 2) {
    var st = pinchState();
    zoom.s = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, pinch.s * (st.dist / pinch.dist)));
    zoom.x = pinch.x + (st.cx - pinch.cx);
    zoom.y = pinch.y + (st.cy - pinch.cy);
    clampZoom();
    applyZoom();
    lastCentroid = centroid();
    return;
  }
  if (panning && pointers.size === 1) {
    zoom.x = panStart.x + (e.clientX - panStart.px);
    zoom.y = panStart.y + (e.clientY - panStart.py);
    clampZoom();
    applyZoom();
    return;
  }
  if (pointers.size === 1 && !multi) {
    if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
    if (moved) {
      var target = clientToFrame(e.clientX, e.clientY);
      var out = { x: target.x, y: target.y };
      // 灵敏度：精准(0.4)光标跟手变慢便于精控，灵敏(1.8)跑得更快
      if (sens !== 1 && lastSent) {
        out.x = lastSent.x + (target.x - lastSent.x) * sens;
        out.y = lastSent.y + (target.y - lastSent.y) * sens;
      }
      setLastFrame(out.x, out.y);
      lastSent = out;
      send({ t: "move", x: out.x, y: out.y });
    }
  } else if (pointers.size === 2) {
    var c = centroid();
    var dy = Math.round((lastCentroid.y - c.y) * 0.7 * wheelSpeed);
    var dx = Math.round((lastCentroid.x - c.x) * 0.5 * wheelSpeed);
    if (dy !== 0 || dx !== 0) send({ t: "scroll", dy: dy, dx: dx, mods: activeMods() });
    lastCentroid = c;
  }
});

function endPointer(e) {
  if (!pointers.has(e.pointerId)) return;
  var p = pointers.get(e.pointerId);
  clearTimeout(pressTimer);
  var wasOne = pointers.size === 1;
  var wasPinch = !!pinch;
  pointers.delete(e.pointerId);
  if (pointers.size < 2) pinch = null;
  if (pointers.size === 0) { panning = false; panStart = null; lastCentroid = null; }
  if (wasPinch || panning) { moved = true; return; }  // 缩放/平移不触发点击
  if (zoom.s > 1.02 && wasOne) {
    // 缩放状态下：双击（非拖动）= 复位
    var now = Date.now();
    if (!moved && lastTap && now - lastTap.t < 300 &&
        Math.abs(p.x - lastTap.x) < 34 && Math.abs(p.y - lastTap.y) < 34) {
      resetZoom();
      lastTap = null;
      return;
    }
    if (!moved) lastTap = { t: now, x: p.x, y: p.y };
    return; // 缩放状态下单击不发送鼠标点击，避免误操作
  }
  if (wasOne && !multi) {
    if (!moved) {
      var f = clientToFrame(p.x, p.y);
      setLastFrame(f.x, f.y);
      var now = Date.now();
      if (lastTap && now - lastTap.t < 300 &&
          Math.abs(p.x - lastTap.x) < 34 && Math.abs(p.y - lastTap.y) < 34) {
        send({ t: "click", button: "left", x: f.x, y: f.y, count: 2, mods: activeMods() });
        lastTap = null;
      } else {
        send({ t: "click", button: "left", x: f.x, y: f.y, mods: activeMods() });
        lastTap = { t: now, x: p.x, y: p.y };
      }
    }
  }
  if (pointers.size === 0) lastCentroid = null;
}
stage.addEventListener("pointerup", endPointer);
stage.addEventListener("pointercancel", endPointer);
stage.addEventListener("contextmenu", function (e) { e.preventDefault(); });

/* ---------- 触摸自愈：指针状态卡死检测 ----------
   浏览器偶发吞掉 pointercancel（切后台/滚动接管/通知横幅），残留的指针
   会让后续触摸全部失灵（表现为"触屏失灵"）。真实触摸不可能 3 秒无事件，
   超时即自动清空全部触摸状态，无需刷新页面。 */
var lastTouchTs = Date.now();
["pointerdown", "pointermove", "pointerup", "pointercancel"].forEach(function (ev) {
  stage.addEventListener(ev, function () { lastTouchTs = Date.now(); }, true);
});
setInterval(function () {
  if (pointers.size > 0 && Date.now() - lastTouchTs > 3000) {
    pointers.clear();
    clearTimeout(pressTimer);
    pinch = null; panning = false; panStart = null; lastCentroid = null;
    multi = false; moved = false; lastSent = null;
  }
}, 1000);
