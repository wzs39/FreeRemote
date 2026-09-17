/* FreeRemote 前端唯一共享状态。
 * 所有模块从这里取共享量（页面参数/连接状态/画质/设置/修饰键），
 * 模块间不互相 import（见 main.js 的装配说明），杜绝单文件时代的隐式全局。 */
"use strict";

/* 页面参数 */
export var params = new URLSearchParams(location.search);
export var token = params.get("token") || "";
export var relay = !!params.get("id");
export var deviceId = params.get("id") || "";
export var pass = params.get("pass") || "";

/* 连接/推流状态 */
export var info = { w: 0, h: 0 };
export var lastOnline = null;     // 中继模式：设备上次在线状态（null=未知）
export var wsOk = false;
export var closedByUs = false;
export var vmode = "none";        // none | blocks | mjpeg
export var vSignal = false;       // 推流是否出现过任何活信号（帧/心跳/init）
export var vLastMsg = 0;          // 最后一次推流消息时间
export var vLastFrame = 0;        // 最后一次真实画面帧时间
export var vws = null;            // 推流 WebSocket（video 模块持有，state 托管）

/* 用户偏好（内存值；持久化在 prefs.js） */
export var quality = "mid";
export var sens = 1;
export var wheelSpeed = 1;
export var mods = { ctrl: false, alt: false, shift: false, win: false };

/* 触摸/点击共享量 */
export var lastFrame = { x: 0, y: 0 };   // 最后一次触摸/点击的帧内坐标

/* 通用工具 */
export function $(id) { return document.getElementById(id); }
export function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
export function activeMods() {
  return Object.keys(mods).filter(function (k) { return mods[k]; });
}

/* URL 与连接地址 */
export function apiUrl(path, extra) {
  extra = extra || "";
  if (relay) return path + "?id=" + encodeURIComponent(deviceId) + "&pass=" + encodeURIComponent(pass) + extra;
  return path + "?token=" + encodeURIComponent(token) + extra;
}
export function streamUrl(q) {
  if (relay) return apiUrl("/stream"); // 画质由 setpreset 指令让电脑端切换
  return apiUrl("/stream", "&q=" + q);
}
export function wsUrl(path) {
  var proto = location.protocol === "https:" ? "wss://" : "ws://";
  return proto + location.host + apiUrl(path);
}
export function ctlUrl() {
  return wsUrl(relay ? "/ctl" : "/ws");
}

/* 发送注入：control 通道的 send 实现在 ctl.js，经 bindSend 反转依赖，
 * 其余模块统一 import { send } —— 打破 ctl↔control/info/quality 的环。
 * 发送前经 proto.sanitize 校验：畸形消息在源头丢弃（服务端契约的前端镜像）。 */
import { sanitize } from "./proto.js";
var _sender = null;
export function bindSend(f) { _sender = f; }
export function send(obj) {
  if (!_sender) return;
  var clean = sanitize(obj);
  if (clean) _sender(clean);
}

/* DOM 引用（initDom 后可用；模块按需 import，避免循环依赖） */
export var screenEl, stage, vcanvas, vctx, ccanvas, cctx, vwrap, view;

export function initDom() {
  screenEl = $("screen");
  stage = $("stage");
  vcanvas = $("vcanvas");
  vctx = vcanvas.getContext("2d");
  ccanvas = $("ccursor");
  cctx = ccanvas.getContext("2d");
  vwrap = $("vwrap");
  view = screenEl; // 当前可见的渲染元素（img 或 canvas）
}

// state.js 在依赖图中最先执行（无自身 import），在此初始化 DOM 引用，
// 保证任何模块体执行时 DOM 引用已就绪（脚本在 </body> 前加载，DOM 已解析）。
initDom();

/* 写访问器：跨模块可变状态统一走 set 系列 / touch 系列（import 的 let 解构会失联） */
export function setRelay(v) { relay = v; }
export function setDeviceId(v) { deviceId = v; }
export function setLastOnline(v) { lastOnline = v; }
export function setWsOk(v) { wsOk = v; }
export function setClosedByUs(v) { closedByUs = v; }
export function setVmode(v) { vmode = v; }
export function setVSignal(v) { vSignal = v; }
export function setVws(v) { vws = v; }
export function touchVMsg() { vLastMsg = Date.now(); }
export function touchVFrame() { vLastFrame = Date.now(); }
export function setQuality(v) { quality = v; }
export function setSens(v) { sens = v; }
export function setWheel(v) { wheelSpeed = v; }
export function setInfo(d) { info = d; }
export function setLastFrame(x, y) { lastFrame.x = x; lastFrame.y = y; }
export function setView(v) { view = v; }

/* 修饰键逻辑状态归 state：gestures 经 activeMods() 读取，control 经钩子同步。
 * 用 window 钩子而非跨模块 import，避免 control↔gestures 互依赖。 */
if (typeof window !== "undefined") {
  window.__frModsSync = function (k, on) { mods[k] = on; };
  window.__frModsReset = function () { Object.keys(mods).forEach(function (k) { mods[k] = false; }); };
}
