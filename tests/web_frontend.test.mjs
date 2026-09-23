/* 前端行为锁：手势状态机（gestures.js）+ WS 重连逻辑（ctl.js）。
 * node 零依赖直跑：DOM / WebSocket / 定时器全部桩化，事件经真实 addEventListener
 * 注册的 handler 驱动。运行：node tests/web_frontend.test.mjs
 *
 * 锁定的关键行为（都曾是真实事故）：
 *  - 手势 handler 的 import 完整性：漏 import 时每次触摸 ReferenceError（触屏失灵）
 *  - 双指批次门控：双指各 ≥6px 位移后才按“相对位移 vs 同向位移”判定捏合/滚动，判定后锁定
 *    （逐事件判定的时序缺陷：平移首指 move 先到、距离剧变，会被误判成捏合并永久锁定）
 *  - pointercancel 与 pointerup 同路径（浏览器吞事件的兜底）
 *  - 触摸自愈：3 秒无触摸事件清空全部指针状态
 *  - 断线 1.5s 自动重连；closedByUs 时不重连；重连后发 releasekeys + 复位修饰键
 */
import assert from "node:assert";

/* ---------- 桩：定时器（可查询、可手动触发） ---------- */
let tid = 0;
const timerQ = [];
global.setTimeout = (fn, ms) => { timerQ.push({ id: ++tid, fn, ms }); return tid; };
global.clearTimeout = (id) => { const i = timerQ.findIndex((t) => t.id === id); if (i >= 0) timerQ.splice(i, 1); };
const intervals = [];
global.setInterval = (fn, ms) => { intervals.push({ fn, ms }); return 0; };
function runTimer(ms) {
  const i = timerQ.findIndex((t) => t.ms === ms);
  assert.ok(i >= 0, `没有 ${ms}ms 定时器`);
  const t = timerQ.splice(i, 1)[0];
  t.fn();
}

/* ---------- 桩：DOM ---------- */
const els = {};
function makeEl(id) {
  const handlers = {};
  const el = {
    id,
    _handlers: handlers,
    addEventListener(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
    _fire(type, ev) { (handlers[type] || []).forEach((fn) => fn(ev)); },
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      toggle(c, force) { const on = force === undefined ? !this._s.has(c) : !!force; if (on) this._s.add(c); else this._s.delete(c); return on; },
      contains(c) { return this._s.has(c); },
    },
    style: {},
    dataset: {},
    textContent: "",
    hidden: false,
    disabled: false,
    clientWidth: 390, clientHeight: 844,
    offsetLeft: 10, offsetTop: 10, offsetWidth: 100, offsetHeight: 40,
    getBoundingClientRect() { return { left: 0, top: 0, width: 1843, height: 1152 }; },
    getContext() {
      return { fillRect() {}, clearRect() {}, drawImage() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, fill() {}, stroke() {}, fillStyle: "", lineWidth: 0 };
    },
    setPointerCapture() {},
  };
  return el;
}
global.document = {
  getElementById(id) { return (els[id] = els[id] || makeEl(id)); },
  createElement(tag) { return makeEl("anon-" + tag); },
  querySelectorAll() { return []; },
  addEventListener() {},
  body: makeEl("body"),
  documentElement: makeEl("html"),
  hidden: false,
};
global.window = {
  addEventListener() {},
  innerWidth: 390, innerHeight: 844,
};
global.location = { search: "?token=T1", protocol: "http:", host: "localhost:8080", pathname: "/" };
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

/* ---------- 桩：WebSocket ---------- */
const wsInstances = [];
class FakeWS {
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; wsInstances.push(this); }
  send(d) { this.sent.push(d); }
  close() { if (this.readyState !== 3) { this.readyState = 3; if (this.onclose) this.onclose({}); } }
}
global.WebSocket = FakeWS;
window.WebSocket = FakeWS;

/* ---------- 导入被测模块（桩就绪后） ---------- */
const state = await import("../web/js/state.js");
const ctl = await import("../web/js/ctl.js"); // 传递闭包含 gestures/prefs/video/bigmode 全部装配

let n = 0;
function t(name, fn) { fn(); n++; console.log("  \u2713 " + name); }
function approx(a, b) { return Math.abs(a - b) < 0.01; }
function sentMsgs() {
  const ws = wsInstances[wsInstances.length - 1];
  return ws.sent.map((s) => JSON.parse(s));
}

/* ================= 一、WS 重连逻辑（ctl.js） ================= */

t("connect 建立 /ws 连接（带 token）", () => {
  ctl.connect();
  const ws = wsInstances[0];
  assert.strictEqual(ws.url, "ws://localhost:8080/ws?token=T1");
});

t("onopen：状态灯绿、发 releasekeys、复位本地修饰键", () => {
  window.__frModsSync("ctrl", true); // 模拟残留的按住 Ctrl
  assert.deepStrictEqual(state.activeMods(), ["ctrl"]);
  const ws = wsInstances[0];
  ws.readyState = 1; // 真实浏览器中 onopen 触发时连接已 OPEN
  ws.onopen();
  assert.strictEqual(els["dot"].className, "dot ok");
  assert.strictEqual(els["status"].textContent, "已连接");
  assert.ok(ws.sent.some((s) => JSON.parse(s).t === "releasekeys"), "重连后必须发 releasekeys");
  assert.deepStrictEqual(state.activeMods(), [], "重连后修饰键必须复位");
});

t("onclose：状态灯红、1.5s 后自动重连", () => {
  const ws = wsInstances[0];
  ws.onclose({});
  assert.strictEqual(els["dot"].className, "dot bad");
  assert.strictEqual(els["status"].textContent, "已断开，重连中…");
  assert.strictEqual(wsInstances.length, 1);
  runTimer(1500);
  assert.strictEqual(wsInstances.length, 2, "1.5s 后必须建新连接");
});

t("closedByUs 时断线不重连", () => {
  state.setClosedByUs(true);
  const ws = wsInstances[1];
  ws.readyState = 1;
  ws.onclose({});
  assert.strictEqual(wsInstances.length, 2, "主动关闭不得重连");
  assert.strictEqual(timerQ.filter((t2) => t2.ms === 1500).length, 0);
  state.setClosedByUs(false);
});

t("send 经 sanitize 白名单重组：合法上线、畸形拦截", () => {
  const ws = wsInstances[1];
  ws.readyState = 1; // 模拟已打开
  state.send({ t: "move", x: 5, y: 6, evil: "x" });
  const last = JSON.parse(ws.sent[ws.sent.length - 1]);
  assert.deepStrictEqual(last, { t: "move", x: 5, y: 6 });
  const before = ws.sent.length;
  state.send({ t: "move", x: "5", y: 6 });   // 字符串坐标
  state.send({ t: "move", x: NaN, y: 6 });   // NaN
  state.send({ t: "bogus" });                // 未知类型
  assert.strictEqual(ws.sent.length, before, "畸形消息不得上线");
});

/* ================= 二、手势状态机（gestures.js，经真实 handler 驱动） ================= */

const stage = state.stage; // 与 gestures.js import 的是同一个元素对象
state.setInfo({ w: 1843, h: 1152 }); // 模拟 fetchInfo 完成（真实应用推流前的帧尺寸）

function down(id, x, y) { stage._fire("pointerdown", { pointerId: id, clientX: x, clientY: y, button: 0, preventDefault() {} }); }
function move(id, x, y) { stage._fire("pointermove", { pointerId: id, clientX: x, clientY: y }); }
function up(id) { stage._fire("pointerup", { pointerId: id }); }
function cancel(id) { stage._fire("pointercancel", { pointerId: id }); }
// 在远离后续测试坐标处垫一次轻点，隔离上一用例残留的 lastTap（300ms 双击窗口）
function primeTap() { down(99, 1500, 900); up(99); }

t("wsOk=false 时触摸完全无效", () => {
  state.setWsOk(false);
  const before = sentMsgs().length;
  down(1, 100, 100); up(1);
  assert.strictEqual(sentMsgs().length, before);
  state.setWsOk(true);
});

t("单击 = 左键点击（帧坐标经 view 矩形换算）", () => {
  down(1, 500, 300); up(1);
  const m = sentMsgs().pop();
  assert.strictEqual(m.t, "click");
  assert.strictEqual(m.button, "left");
  assert.ok(approx(m.x, 500) && approx(m.y, 300), `坐标换算错误: ${m.x},${m.y}`);
  assert.strictEqual(m.count, 1);
});

t("拖动 = move 指令，松手不产生点击", () => {
  const before = sentMsgs().length;
  down(1, 500, 300);
  move(1, 560, 310); // |dx|+|dy| = 70 > 4 → moved
  up(1);
  const msgs = sentMsgs().slice(before);
  assert.ok(msgs.some((m) => m.t === "move" && m.x === 560 && m.y === 310), "必须发 move");
  assert.ok(!msgs.some((m) => m.t === "click"), "拖动后不得点击");
});

t("双击（300ms 内二次轻点）= count:2", () => {
  primeTap(); // 消耗可能的残留 lastTap
  down(1, 500, 300); up(1);   // 第一次轻点：与垫点距离远 → 单击，记录 lastTap
  down(1, 510, 310); up(1);   // 34px 内 + 300ms 内 → 双击
  const m = sentMsgs().pop();
  assert.strictEqual(m.count, 2, "第二次轻点必须是双击");
});

t("长按 500ms = 右键，且之后 pointerup 不重复发送", () => {
  const before = sentMsgs().length;
  down(1, 500, 300);
  runTimer(500); // pressTimer
  const msgs = sentMsgs().slice(before);
  assert.ok(msgs.some((m) => m.t === "click" && m.button === "right" && approx(m.x, 500) && approx(m.y, 300)), "长按必须发右键");
  up(1); // 残指已被长按逻辑删除，不应再发消息
  assert.strictEqual(sentMsgs().slice(before).filter((m) => m.t === "click").length, 1);
});

t("pointercancel 与 pointerup 同路径：取消也产生点击", () => {
  primeTap();
  down(1, 500, 300);
  cancel(1);
  const m = sentMsgs().pop();
  assert.strictEqual(m.t, "click");
  assert.ok(approx(m.x, 500) && approx(m.y, 300));
  assert.strictEqual(m.count, 1);
});

t("双指平移（双指都有位移后判定）= 滚轮", () => {
  primeTap();
  const before = sentMsgs().length;
  down(1, 400, 400); down(2, 500, 500);
  move(1, 450, 450);            // 仅一指动：歧义态，不得判定也不得发滚动
  assert.strictEqual(sentMsgs().slice(before).filter((m) => m.t === "scroll").length, 0, "歧义态不得发滚动");
  move(2, 550, 550);            // 双指都动（各 50px）：同向主导 → 滚动
  const msgs = sentMsgs().slice(before);
  const scrolls = msgs.filter((m) => m.t === "scroll");
  assert.ok(scrolls.length >= 1, "双指平移必须产生 scroll");
  assert.deepStrictEqual([scrolls[0].dy, scrolls[0].dx], [-35, -25], "滚动量=质心位移×系数");
  assert.ok(!msgs.some((m) => m.t === "click"), "双指手势不得触发点击");
  up(1); up(2);
});

t("双指相对位移（捏开）= 捏合缩放（无 scroll 上线）", () => {
  primeTap();
  const before = sentMsgs().length;
  down(1, 400, 400); down(2, 500, 500);
  move(1, 380, 380);            // 仅一指动：歧义态
  move(2, 520, 520);            // 相对位移主导 → 捏合，缩放 1.4
  const msgs = sentMsgs().slice(before);
  assert.ok(!msgs.some((m) => m.t === "scroll"), "捏合不得产生 scroll");
  assert.ok(!msgs.some((m) => m.t === "click"), "捏合不得触发点击");
  assert.match(els["zoombox"].style.transform, /scale\(1\.4/, "缩放倍数应为 1.4");
  up(1); up(2);
});

t("捏合判定锁定：已判捏合后同向平移不回落成滚动", () => {
  const before = sentMsgs().length;
  down(1, 400, 400); down(2, 500, 500);
  move(1, 380, 380);            // 歧义态
  move(2, 540, 540);            // 相对位移主导 → 判定捏合并锁定
  move(1, 420, 420); move(2, 560, 560); // 同向平移（距离不变）→ 仍锁定捏合
  const scrolls = sentMsgs().slice(before).filter((m) => m.t === "scroll");
  assert.strictEqual(scrolls.length, 0, "捏合锁定后不得回落成滚动");
  up(1); up(2);
  // 复位缩放：缩放态双击 = resetZoom（两次轻点，均不发点击）
  const before2 = sentMsgs().length;
  down(1, 500, 300); up(1); down(1, 505, 305); up(1);
  assert.ok(sentMsgs().slice(before2).every((m) => m.t !== "click"), "缩放态轻点不得发点击");
  assert.strictEqual(els["zoombox"].style.transform, "translate(0px,0px) scale(1)", "双击应复位缩放");
});

t("触摸自愈：3 秒无事件清空卡死指针，触摸恢复", () => {
  const heal = intervals.find((i) => i.ms === 1000);
  assert.ok(heal, "必须有 1s 自愈定时器");
  const nowReal = Date.now;
  down(1, 500, 300); // 指针按下后"卡死"（不再有任何事件）
  Date.now = () => nowReal() + 4000; // 快进 4s
  heal.fn();
  Date.now = nowReal;
  // 卡死指针被清：move 不再响应（map 已空）
  const before = sentMsgs().length;
  move(1, 600, 600);
  assert.strictEqual(sentMsgs().length, before, "自愈后残留指针必须失效");
  // 新触摸完全正常（先垫点隔离残留 lastTap）
  primeTap();
  down(2, 500, 300); up(2);
  const m = sentMsgs().pop();
  assert.strictEqual(m.t, "click");
  assert.ok(approx(m.x, 500) && approx(m.y, 300), `自愈后新触摸应正常: ${m.x},${m.y}`);
  assert.strictEqual(m.count, 1);
});

console.log(`\n${n} 项全部通过`);
