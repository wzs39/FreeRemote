/* 启动装配：模块加载顺序与入口。
 *
 * 装配原则：state 是唯一共享状态；模块间不互相 import（仅 main 做装配），
 * 两个刻意例外：ctl→control/prefs（重连自愈需要）、prefs→view/video/bigmode
 * （偏好应用需要）。send 经 state.bindSend 依赖反转，全模块零环。 */
import { initDom, relay, setRelay } from "./state.js";
import { connect } from "./ctl.js";          // 先加载：注册 send 依赖反转
import { fetchInfo } from "./info.js";
import { startVideo } from "./video.js";
import { cleanUrl, showAuth } from "./login.js";
import "./gestures.js";
import "./control.js";
import "./files.js";
import "./keyboard.js";
import "./prefs.js";
import "./bigmode.js";
import "./view.js";
import "./mjpeg.js";
import "./quality.js";

var wakeLock = null;
function requestWakeLock() {
  if (navigator.wakeLock && !wakeLock) {
    navigator.wakeLock.request("screen")
      .then(function (wl) {
        wakeLock = wl;
        wl.addEventListener("release", function () { wakeLock = null; }); // 释放后允许重新申请
      })
      .catch(function () { /* 不支持时静默 */ });
  }
}
document.addEventListener("visibilitychange", function () {
  if (!document.hidden) {
    requestWakeLock();
    // 切回前台：若画面已冻结超阈值，立即触发检测（不等下一个 3 秒 tick）
    // （vmode/vws/vLastMsg 动态读自 state，此处用活动导入即可）
    import("./state.js").then(function (s) {
      if (s.vmode === "blocks" && s.vws && Date.now() - s.vLastMsg > 12000) {
        try { s.vws.close(); } catch (err) {}
      }
    });
  }
});

function boot() {
  fetchInfo().then(function (ok) {
    if (ok === false) { showAuth(); return; }
    cleanUrl();
    if (relay) setInterval(fetchInfo, 3000);
    connect();
    startVideo();
    requestWakeLock();
  });
}
fetch("/mode").then(function (r) { return r.json(); })
  .then(function (m) { if (m.mode === "relay") setRelay(true); })
  .catch(function () {})
  .then(boot);
