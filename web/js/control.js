/* 工具栏与控制动作：左右中键/滚轮/修饰键/CAD/解锁键盘/自检/唤醒/全屏。 */
import { $, send, apiUrl, deviceId, lastFrame, activeMods } from "./state.js";

export function clickAtFrame(button) {
  send({ t: "click", button: button, x: lastFrame.x, y: lastFrame.y, mods: activeMods() });
}

$("b-left").addEventListener("click", function () { clickAtFrame("left"); });
$("b-right").addEventListener("click", function () { clickAtFrame("right"); });
$("b-mid").addEventListener("click", function () { clickAtFrame("middle"); });
$("b-wup").addEventListener("click", function () { send({ t: "scroll", dy: 3, dx: 0, mods: activeMods() }); });
$("b-wdown").addEventListener("click", function () { send({ t: "scroll", dy: -3, dx: 0, mods: activeMods() }); });

export function resetLocalMods() {
  var mods = { ctrl: 1, alt: 1, shift: 1, win: 1 };
  document.querySelectorAll(".mod").forEach(function (b) {
    b.classList.remove("on");
    if (b.dataset.mod && mods[b.dataset.mod]) mods[b.dataset.mod] = 0;
  });
  // 逻辑状态在 state.mods（同源模块经 import 读写）；此处仅重置 UI 与标志
  Object.keys(mods).forEach(function (k) { delete mods[k]; });
  if (window.__frModsReset) window.__frModsReset();
}

document.querySelectorAll(".mod").forEach(function (b) {
  b.addEventListener("click", function () {
    var k = b.dataset.mod;
    var on = b.classList.toggle("on");
    if (on) send({ t: "keydown", key: k });
    else send({ t: "keyup", key: k });
    if (window.__frModsSync) window.__frModsSync(k, on);
  });
});

/* ---------- 解锁键盘：一键释放电脑端所有修饰键（操作"全部失灵"自救） ---------- */
$("b-unstick").addEventListener("click", function () {
  resetLocalMods();
  send({ t: "releasekeys" });
  var prev = $("status").textContent;
  $("status").textContent = "已释放 Ctrl/Alt/Shift/Win —— 再试一下点击";
  setTimeout(function () { $("status").textContent = prev; }, 2500);
});

$("b-doctor").addEventListener("click", function () {
  window.open("/doctor" + location.search, "_blank");
});

/* ---------- 远程唤醒（WoL）：中继需 --wol "识别码:MAC" 配置 ---------- */
$("b-wake").addEventListener("click", function () {
  var btn = $("b-wake");
  btn.disabled = true;
  $("status").textContent = "正在发送唤醒包…";
  fetch(apiUrl("/wake"), { method: "POST", credentials: "same-origin" })
    .then(function (r) { return r.json().catch(function () { return { ok: false, error: "HTTP " + r.status }; }); })
    .then(function (d) {
      if (d.ok) {
        $("status").textContent = "⚡ 已发送唤醒包，等待电脑开机（约 30~60 秒）…";
        setTimeout(function () { $("status").textContent = "识别码 " + deviceId + " · 设备离线"; }, 6000);
      } else {
        $("status").textContent = "唤醒失败：" + (d.error || "未配置 WoL");
        setTimeout(function () { $("status").textContent = "识别码 " + deviceId + " · 设备离线"; }, 6000);
      }
    })
    .catch(function () { $("status").textContent = "唤醒失败：网络错误"; })
    .then(function () { btn.disabled = false; });
});

$("b-fs").addEventListener("click", function () {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch(function () {});
});

$("b-cad").addEventListener("click", function () {
  send({ t: "combo", keys: ["ctrl", "alt", "del"] });
  send({ t: "combo", keys: ["ctrl", "shift", "esc"] }); // 兜底打开任务管理器
});
