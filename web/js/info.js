/* 信息轮询与在线状态（/info）：返回 true = 已通过鉴权。 */
import {
  $, apiUrl, relay, deviceId, info, lastOnline, vmode,
  setRelay, setDeviceId, setLastOnline, setInfo, setQuality, setLastFrame,
  screenEl,
} from "./state.js";
import { startVideo } from "./video.js";

function syncQualityButtons(q) {
  document.querySelectorAll("#qseg button").forEach(function (b) {
    b.classList.toggle("on", b.dataset.q === q);
  });
}

export function fetchInfo() {
  return fetch(apiUrl("/info")).then(function (r) {
    if (r.status === 403) return null;
    return r.json();
  }).then(function (d) {
    if (!d) return false;
    // 登录后（URL 无参数）从响应识别运行模式与设备识别码
    if (d.mode === "relay") { setRelay(true); if (d.device_id) setDeviceId(d.device_id); }
    if (relay) {
      var online = !!d.online;
      if (d.w) { info.w = d.w; info.h = d.h; }
      if (d.preset) info.preset = d.preset;
      if (online && online !== lastOnline) {
        // 设备刚上线：重新建立推流
        if (vmode === "none") startVideo();
        else if (vmode === "mjpeg") screenEl.src = apiUrl("/stream", "&ts=" + Date.now());
      }
      setLastOnline(online);
      $("dot").className = "dot " + (online ? "ok" : "bad");
      $("status").textContent = "识别码 " + deviceId + " · " + (online ? "在线" : "设备离线");
      // 设备离线时显示「⚡ 唤醒」按钮（需中继配置了 --wol 才能生效）
      $("b-wake").hidden = online;
      if (info.w) {
        $("res").textContent = info.w + "×" + info.h + " · " + (info.preset || "");
        setLastFrame(info.w / 2, info.h / 2);
      }
      // 画质按钮与设备当前设置同步
      if (d.preset) { setQuality(d.preset); syncQualityButtons(d.preset); }
      return true;
    }
    setInfo(d);
    $("res").textContent = d.w + "×" + d.h + " · " + d.preset;
    setLastFrame(d.w / 2, d.h / 2);
    return true;
  }).catch(function () { return false; });
}
