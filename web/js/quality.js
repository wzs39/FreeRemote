/* 画质切换（顶栏三档）：控制通道下发 setpreset，电脑端本地换编码参数。 */
import { $, send, quality, setQuality, vmode } from "./state.js";
import { screenEl, streamUrl } from "./state.js";
import { fetchInfo } from "./info.js";

export function syncQualityUI(q) {
  document.querySelectorAll("#qseg button").forEach(function (b) {
    b.classList.toggle("on", b.dataset.q === q);
  });
}

export function changeQuality(q) {
  if (q === quality) return;
  setQuality(q);
  syncQualityUI(q);
  send({ t: "setpreset", preset: q }); // 控制通道下发，电脑端本地换编码参数
  if (vmode === "mjpeg") screenEl.src = streamUrl(q);
  setTimeout(fetchInfo, 700);
}

document.querySelectorAll("#qseg button").forEach(function (b) {
  b.addEventListener("click", function () { changeQuality(b.dataset.q); });
});
