/* proto.sanitize 行为锁：畸形消息必须在源头返回 null，合法消息语义不变。
 * 零依赖 ES 模块，node 直接跑：node tests/web_proto.test.mjs
 * 规则与 free_remote/command.py 服务端契约镜像——改服务端时同步改。 */
import assert from "node:assert";
import { sanitize } from "../web/js/proto.js";

var n = 0;
function ok(msg, name) { var out = sanitize(msg); assert.notStrictEqual(out, null, "不应拒绝: " + name); n++; return out; }
function no(msg, name) { assert.strictEqual(sanitize(msg), null, "应拒绝: " + name); n++; }

/* ---- move：数字坐标、拒 NaN/inf/负数/字符串/丢字段 ---- */
assert.deepStrictEqual(ok({ t: "move", x: 100.5, y: 20 }, "move 合法"), { t: "move", x: 100.5, y: 20 });
no({ t: "move", x: NaN, y: 1 }, "move NaN");
no({ t: "move", x: 1, y: Infinity }, "move inf");
no({ t: "move", x: -1, y: 0 }, "move 负坐标");
no({ t: "move", x: "10", y: 0 }, "move 字符串坐标");
no({ t: "move", y: 0 }, "move 丢 x");
no({ t: "move" }, "move 全丢");

/* ---- click：按钮白名单、count 钳制（镜像服务端 1–3）---- */
assert.deepStrictEqual(ok({ t: "click", button: "left", x: 1, y: 2, count: 2 }, "click 合法"),
  { t: "click", button: "left", x: 1, y: 2, count: 2 });
assert.deepStrictEqual(ok({ t: "click", button: "LEFT", x: 1, y: 2 }, "click 大写按钮归一"), { t: "click", button: "left", x: 1, y: 2, count: 1 });
assert.deepStrictEqual(ok({ t: "click", button: "side", x: 1, y: 2 }, "click 未知按钮归一"), { t: "click", button: "left", x: 1, y: 2, count: 1 });
assert.deepStrictEqual(ok({ t: "click", x: 1, y: 2, count: 999 }, "click count 钳上"), { t: "click", button: "left", x: 1, y: 2, count: 3 });
assert.deepStrictEqual(ok({ t: "click", x: 1, y: 2, count: -7 }, "click count 钳下"), { t: "click", button: "left", x: 1, y: 2, count: 1 });
assert.deepStrictEqual(ok({ t: "click", x: 1, y: 2, count: "3" }, "click count 非数字兜底"), { t: "click", button: "left", x: 1, y: 2, count: 1 });
no({ t: "click", button: "left", x: NaN, y: 2 }, "click NaN 坐标");
no({ t: "click", button: "left" }, "click 丢坐标");

/* ---- scroll：有限数、钳 ±99 ---- */
assert.deepStrictEqual(ok({ t: "scroll", dy: 3, dx: 0 }, "scroll 合法"), { t: "scroll", dy: 3, dx: 0 });
assert.deepStrictEqual(ok({ t: "scroll", dy: 500, dx: 0 }, "scroll 钳制"), { t: "scroll", dy: 99, dx: 0 });
no({ t: "scroll", dy: "3", dx: 0 }, "scroll 字符串 dy");
no({ t: "scroll" }, "scroll 丢字段");

/* ---- keydown/keyup/press：键名白名单 ---- */
assert.deepStrictEqual(ok({ t: "press", key: "enter" }, "press 合法"), { t: "press", key: "enter" });
assert.deepStrictEqual(ok({ t: "keydown", key: "ctrl" }, "keydown 修饰键"), { t: "keydown", key: "ctrl" });
no({ t: "keydown", key: "W" }, "keydown 大写未知键");
no({ t: "press", key: "X1" }, "press 鼠标侧键名");
no({ t: "keyup" }, "keyup 丢 key");
no({ t: "press", key: 5 }, "press 非字符串");

/* ---- combo：数组、1–4 键、全部白名单 ---- */
assert.deepStrictEqual(ok({ t: "combo", keys: ["ctrl", "shift", "esc"] }, "combo 合法"), { t: "combo", keys: ["ctrl", "shift", "esc"] });
assert.deepStrictEqual(ok({ t: "combo", keys: ["f5"] }, "combo 单键"), { t: "combo", keys: ["f5"] });
no({ t: "combo", keys: [] }, "combo 空");
no({ t: "combo", keys: ["ctrl", "ctrl", "ctrl", "ctrl", "ctrl"] }, "combo 超 4 键");
no({ t: "combo", keys: ["ctrl", "w"] }, "combo 含未知键");
no({ t: "combo", keys: "ctrl+c" }, "combo 非数组");

/* ---- text：非空字符串、长度上限 ---- */
assert.deepStrictEqual(ok({ t: "text", text: "你好 world" }, "text 合法"), { t: "text", text: "你好 world" });
no({ t: "text", text: "" }, "text 空");
no({ t: "text" }, "text 丢字段");
no({ t: "text", text: "x".repeat(2001) }, "text 超长");
assert.deepStrictEqual(ok({ t: "text", text: "x".repeat(2000) }, "text 2000 边界"), { t: "text", text: "x".repeat(2000) });

/* ---- setres / setpreset：镜像服务端钳制与档位 ---- */
assert.deepStrictEqual(ok({ t: "setres", scale: null }, "setres auto"), { t: "setres", scale: null });
assert.deepStrictEqual(ok({ t: "setres", scale: 0.1 }, "setres 钳下"), { t: "setres", scale: 0.2 });
assert.deepStrictEqual(ok({ t: "setres", scale: 9 }, "setres 钳上"), { t: "setres", scale: 2 });
no({ t: "setres", scale: NaN }, "setres NaN 后门");
no({ t: "setres", scale: "0.5" }, "setres 字符串");
no({ t: "setpreset", preset: "ultra" }, "setpreset 未知档位");
assert.deepStrictEqual(ok({ t: "setpreset", preset: "high" }, "setpreset 合法"), { t: "setpreset", preset: "high" });

/* ---- releasekeys / 未知类型 / 顶层非对象 ---- */
assert.deepStrictEqual(ok({ t: "releasekeys" }, "releasekeys"), { t: "releasekeys" });
no({ t: "mystery" }, "未知类型");
no({ t: "click" }, "已知类型但必填字段全缺");
no("5"), no(42), no(null), no(undefined), no([1, 2]); n += 5;

/* ---- mods：过滤白名单外成员，全非法则丢弃字段 ---- */
assert.deepStrictEqual(ok({ t: "press", key: "enter", mods: ["ctrl", "W"] }, "mods 过滤"), { t: "press", key: "enter", mods: ["ctrl"] });
var dropped = ok({ t: "press", key: "enter", mods: ["W"] }, "mods 全非法");
assert.strictEqual(dropped.mods, undefined, "mods 全非法应整体丢弃");
assert.deepStrictEqual(ok({ t: "press", key: "enter" }, "无 mods"), { t: "press", key: "enter" });

/* ---- 不泄漏多余字段（白名单重组，不透传原对象）---- */
var clean = ok({ t: "move", x: 1, y: 2, evil: "payload", count: 99 }, "move 带多余字段");
assert.deepStrictEqual(Object.keys(clean).sort(), ["t", "x", "y"], "move 只保留契约字段");

console.log("web_proto 行为锁: " + n + " 项全部通过");
