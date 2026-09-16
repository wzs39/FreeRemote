/* 虚拟键盘面板：特殊键、常用组合键、文本输入（中文走剪贴板路径）。 */
import { $, send, activeMods } from "./state.js";

var specialKeys = [
  ["Esc", "esc"], ["Enter", "enter"], ["Tab", "tab"],
  ["⌫", "backspace"], ["Del", "delete"], ["空格", "space"],
  ["↑", "up"], ["↓", "down"], ["←", "left"], ["→", "right"],
  ["Home", "home"], ["End", "end"], ["PgUp", "pageup"], ["PgDn", "pagedown"]
];
var kbGrid = $("kbkeys");
specialKeys.forEach(function (pair) {
  var b = document.createElement("button");
  b.textContent = pair[0];
  b.addEventListener("click", function () { send({ t: "press", key: pair[1], mods: activeMods() }); });
  kbGrid.appendChild(b);
});

// 常用快捷键组合（第二排）
var comboKeys = [
  ["Ctrl+C", ["ctrl", "c"]], ["Ctrl+V", ["ctrl", "v"]], ["Ctrl+X", ["ctrl", "x"]],
  ["Ctrl+Z", ["ctrl", "z"]], ["Ctrl+A", ["ctrl", "a"]], ["Ctrl+S", ["ctrl", "s"]],
  ["F5", ["f5"]], ["Win+D", ["win", "d"]], ["Win+L", ["win", "l"]],
  ["任务管理器", ["ctrl", "shift", "esc"]]
];
var kbCombo = $("kbcombo");
comboKeys.forEach(function (pair) {
  var b = document.createElement("button");
  b.textContent = pair[0];
  b.addEventListener("click", function () {
    if (pair[1].length === 1) send({ t: "press", key: pair[1][0], mods: activeMods() });
    else send({ t: "combo", keys: pair[1] });
  });
  kbCombo.appendChild(b);
});

function toggleKb(show) {
  $("kbpanel").classList.toggle("show", show);
  if (show) $("kb-input").focus();
}
$("b-kb").addEventListener("click", function () {
  toggleKb(!$("kbpanel").classList.contains("show"));
});
$("kb-send").addEventListener("click", sendText);
$("kb-input").addEventListener("keydown", function (e) {
  if (e.isComposing || e.keyCode === 229) return;  // 中文输入法组词中的回车不发送
  if (e.key === "Enter") { sendText(); e.preventDefault(); }
  e.stopPropagation();
});
function sendText() {
  var v = $("kb-input").value;
  if (!v) return;
  send({ t: "text", text: v, mods: activeMods() });
  $("kb-input").value = "";
  $("kb-input").focus(); // 连打：发送后保持焦点，可继续输入
}
