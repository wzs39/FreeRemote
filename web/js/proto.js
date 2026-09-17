/* 指令协议校验 —— 发送前唯一拦截点（state.send 调用）。
 * 规则与 free_remote/command.py 的服务端契约镜像：数字坐标必须为有限数、
 * 按钮白名单、count 钳制、NaN/inf 拒绝。畸形消息在源头丢弃（返回 null），
 * 不会到达 WS。修改服务端契约时同步改这里（tests 有协议锁）。 */
"use strict";

var BTN = { left: 1, right: 1, middle: 1 };
var KEY = {
  ctrl: 1, alt: 1, shift: 1, win: 1,
  esc: 1, enter: 1, tab: 1, backspace: 1, delete: 1, del: 1, space: 1,
  up: 1, down: 1, left: 1, right: 1, home: 1, end: 1, pageup: 1, pagedown: 1, f5: 1,
  a: 1, c: 1, v: 1, x: 1, z: 1, s: 1, d: 1, l: 1
};

function isNum(v) { return typeof v === "number" && isFinite(v); }
function cleanMods(v) {
  if (!Array.isArray(v)) return undefined;
  var out = v.filter(function (k) { return KEY[k] === 1 && (k === "ctrl" || k === "alt" || k === "shift" || k === "win"); });
  return out.length ? out : undefined;
}

export function sanitize(msg) {
  if (!msg || typeof msg !== "object") return null;
  var t = msg.t;
  var out = { t: t };

  if (t === "move" || t === "click") {
    if (!isNum(msg.x) || !isNum(msg.y) || msg.x < 0 || msg.y < 0) return null;
    out.x = msg.x; out.y = msg.y;
  }
  if (t === "click") {
    out.button = BTN[msg.button] ? msg.button : "left";
    var n = msg.count === undefined ? 1 : msg.count;
    if (!isNum(n)) n = 1;
    out.count = Math.max(1, Math.min(3, Math.round(n)));
  }
  if (t === "scroll") {
    if (!isNum(msg.dx) || !isNum(msg.dy)) return null;
    out.dx = Math.max(-99, Math.min(99, Math.round(msg.dx)));
    out.dy = Math.max(-99, Math.min(99, Math.round(msg.dy)));
  }
  if (t === "keydown" || t === "keyup" || t === "press") {
    if (typeof msg.key !== "string" || !KEY[msg.key]) return null;
    out.key = msg.key;
  }
  if (t === "combo") {
    if (!Array.isArray(msg.keys) || msg.keys.length === 0 || msg.keys.length > 4) return null;
    var keys = [];
    for (var i = 0; i < msg.keys.length; i++) {
      var k = msg.keys[i];
      if (typeof k !== "string" || !KEY[k]) return null;
      keys.push(k);
    }
    out.keys = keys;
  }
  if (t === "text") {
    var s = msg.text;
    if (typeof s !== "string" || s.length === 0 || s.length > 2000) return null;
    out.text = s;
  }
  if (t === "setres") {
    if (msg.scale === null || msg.scale === undefined) out.scale = null;
    else {
      if (!isNum(msg.scale)) return null;
      out.scale = Math.max(0.2, Math.min(2, msg.scale));
    }
  }
  if (t === "setpreset") {
    if (msg.preset !== "low" && msg.preset !== "mid" && msg.preset !== "high") return null;
    out.preset = msg.preset;
  }
  if (t !== "releasekeys" && out.t === t && Object.keys(out).length === 1) return null; // 未知类型

  if (msg.mods !== undefined) {
    var m = cleanMods(msg.mods);
    if (m) out.mods = m;
  }
  return out;
}
