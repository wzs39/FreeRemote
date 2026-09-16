/* 大屏模式：悬浮控制条可拖拽（按住拖动，轻点仍是按键），位置持久化。 */
import { $ } from "./state.js";

var bar = $("toolbar"), barDrag = null, barDragged = false, barPointer = null;

function barSaved() {
  try { return JSON.parse(localStorage.getItem("fr-prefs") || "{}").barPos || null; }
  catch (e) { return null; }
}
function barSavePos(x, y) {
  try {
    var p = JSON.parse(localStorage.getItem("fr-prefs") || "{}");
    p.barPos = { x: Math.round(x), y: Math.round(y) };
    localStorage.setItem("fr-prefs", JSON.stringify(p));
  } catch (e) {}
}
function barClamp(x, y) {
  return {
    x: Math.max(6, Math.min(window.innerWidth - bar.offsetWidth - 6, x)),
    y: Math.max(6, Math.min(window.innerHeight - bar.offsetHeight - 6, y))
  };
}
function barPlace(x, y, save) {
  var c = barClamp(x, y);
  bar.style.left = c.x + "px"; bar.style.top = c.y + "px";
  bar.style.right = "auto"; bar.style.bottom = "auto";
  if (save) barSavePos(c.x, c.y);
}
export function barRestore() {
  if (!document.body.classList.contains("big")) return;
  var p = barSaved();
  if (p) barPlace(p.x, p.y, false);
}
bar.addEventListener("pointerdown", function (e) {
  if (!document.body.classList.contains("big") || barPointer !== null) return;
  barPointer = e.pointerId;
  barDrag = { px: e.clientX, py: e.clientY, x: bar.offsetLeft, y: bar.offsetTop };
  barDragged = false;
});
bar.addEventListener("pointermove", function (e) {
  if (barPointer !== e.pointerId || !barDrag) return;
  var dx = e.clientX - barDrag.px, dy = e.clientY - barDrag.py;
  if (!barDragged && Math.abs(dx) + Math.abs(dy) < 8) return; // 小于阈值不算拖拽
  barDragged = true;
  barPlace(barDrag.x + dx, barDrag.y + dy, false);
});
function barDragEnd(e) {
  if (barPointer !== e.pointerId) return;
  barPointer = null;
  if (barDragged) {
    barPlace(bar.offsetLeft, bar.offsetTop, true); // 松手时保存位置
    setTimeout(function () { barDragged = false; }, 0); // click 抑制后复位
  }
  barDrag = null;
}
bar.addEventListener("pointerup", barDragEnd);
bar.addEventListener("pointercancel", barDragEnd);
bar.addEventListener("click", function (e) {
  if (barDragged) {
    e.stopPropagation(); e.preventDefault();
    barDragged = false; // 同步复位，连续两次拖拽也可靠
  }
}, true);
window.addEventListener("resize", function () {
  if (document.body.classList.contains("big") && bar.style.left) barPlace(bar.offsetLeft, bar.offsetTop, true);
});
