/* MJPEG 降级保命路径：<img> 推流 + 断流自动重试。 */
import { screenEl, vwrap, setView, apiUrl, streamUrl, relay, vmode, quality, setVmode } from "./state.js";

export function startMjpeg() {
  setVmode("mjpeg");
  vwrap.classList.remove("show");
  screenEl.style.display = "";
  setView(screenEl);
  screenEl.src = streamUrl(quality);
}

screenEl.addEventListener("error", function () {
  if (relay && vmode === "mjpeg") setTimeout(function () {
    screenEl.src = apiUrl("/stream", "&ts=" + Date.now());
  }, 2000);
});
