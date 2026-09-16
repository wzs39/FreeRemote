/* 操作设置与持久化：灵敏度/滚轮/分辨率/初始大小/单手/大屏。 */
import { $, send, sens, wheelSpeed, setSens, setWheel } from "./state.js";
import { applyFit } from "./view.js";
import { fitCanvas } from "./video.js";
import { barRestore } from "./bigmode.js";

export var resScale = "auto";
export var fitMode = "contain";

function loadPrefs() {
  try {
    var s = JSON.parse(localStorage.getItem("fr-prefs") || "{}");
    if (typeof s.sens === "number") setSens(s.sens);
    if (typeof s.wheel === "number") setWheel(s.wheel);
    if (s.res !== undefined) resScale = s.res;
    if (s.fit !== undefined) fitMode = s.fit;
    if (s.onehand) document.body.classList.add("onehand");
    if (s.big) document.body.classList.add("big");
  } catch (e) {}
  applyFit();
  syncPrefsUI();
}
function syncPrefsUI() {
  document.querySelectorAll("#set-sens button").forEach(function (b) {
    b.classList.toggle("on", parseFloat(b.dataset.v) === sens);
  });
  document.querySelectorAll("#set-wheel button").forEach(function (b) {
    b.classList.toggle("on", parseFloat(b.dataset.v) === wheelSpeed);
  });
  document.querySelectorAll("#set-res button").forEach(function (b) {
    b.classList.toggle("on", b.dataset.v === String(resScale));
  });
  var oh = document.body.classList.contains("onehand");
  $("set-onehand").classList.toggle("on", oh);
  $("set-onehand").textContent = oh ? "开" : "关";
  var big = document.body.classList.contains("big");
  $("set-big").classList.toggle("on", big);
  $("set-big").textContent = big ? "开" : "关";
  document.querySelectorAll("#set-fit button").forEach(function (b) {
    b.classList.toggle("on", b.dataset.v === fitMode);
  });
}
function savePrefs() {
  try {
    localStorage.setItem("fr-prefs", JSON.stringify({
      sens: sens, wheel: wheelSpeed, res: resScale, fit: fitMode,
      onehand: document.body.classList.contains("onehand"),
      big: document.body.classList.contains("big")
    }));
  } catch (e) {}
}
/* 画面分辨率：改变推流帧的像素尺寸（服务器 setres 指令） */
export function changeRes(v) {
  resScale = v === "auto" ? "auto" : parseFloat(v);
  if (v === "auto") send({ t: "setres", scale: null });
  else send({ t: "setres", scale: parseFloat(v) });
  setTimeout(function () { import("./info.js").then(function (m) { m.fetchInfo(); }); }, 700);
}
$("b-set").addEventListener("click", function () {
  $("setpanel").hidden = !$("setpanel").hidden;
});
$("set-close").addEventListener("click", function () { $("setpanel").hidden = true; });
// 设置面板分段按钮统一绑定：sens/wheel 直接存值，res/fit 走各自 apply 函数
[["#set-sens", function (v) { setSens(parseFloat(v)); }],
 ["#set-wheel", function (v) { setWheel(parseFloat(v)); }],
 ["#set-res", function (v) { changeRes(v); }],
 ["#set-fit", function (v) { fitMode = v; applyFit(); }]].forEach(function (pair) {
  document.querySelectorAll(pair[0] + " button").forEach(function (b) {
    b.addEventListener("click", function () {
      pair[1](b.dataset.v); syncPrefsUI(); savePrefs();
    });
  });
});
$("set-onehand").addEventListener("click", function () {
  document.body.classList.toggle("onehand");
  syncPrefsUI(); savePrefs();
});
$("set-big").addEventListener("click", function () {
  document.body.classList.toggle("big");
  syncPrefsUI(); savePrefs();
  if (document.body.classList.contains("big")) setTimeout(barRestore, 0); // 恢复上次停靠位置
});
$("big-exit").addEventListener("click", function () {
  document.body.classList.remove("big");
  syncPrefsUI(); savePrefs();
});
loadPrefs();
