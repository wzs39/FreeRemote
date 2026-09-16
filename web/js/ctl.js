/* 控制通道 WebSocket（/ws 或中继 /ctl）：连接、断线 1.5s 重连、重连自愈。 */
import { $, ctlUrl, relay, closedByUs, setWsOk } from "./state.js";
import { bindSend } from "./state.js";
import { resetLocalMods } from "./control.js";
import { changeRes, resScale } from "./prefs.js";

var ws = null;
var reconnectTimer = null;

function sendCtl(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}
bindSend(sendCtl); // 其余模块统一 import { send } from state（依赖反转）

export function connect() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  ws = new WebSocket(ctlUrl());
  ws.onopen = function () {
    setWsOk(true);
    $("dot").className = "dot ok";
    if (!relay) $("status").textContent = "已连接";
    // 重连后同步键盘状态：电脑端可能残留卡键（上次断线时正按着修饰键），先强制释放
    sendCtl({ t: "releasekeys" });
    resetLocalMods();
    if (resScale !== "auto") changeRes(resScale); // 重连后恢复自定义分辨率
  };
  ws.onclose = function () {
    setWsOk(false);
    $("dot").className = "dot bad";
    $("status").textContent = "已断开，重连中…";
    if (!closedByUs) reconnectTimer = setTimeout(connect, 1500);
  };
  ws.onerror = function () { try { ws.close(); } catch (e) {} };
}
