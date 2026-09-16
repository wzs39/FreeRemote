/* 画面初始大小（适应/铺宽/铺满）与缩放复位入口。fitMode 的唯一所有者。 */
import { $, vmode } from "./state.js";
import { resetZoom } from "./gestures.js";
import { fitCanvas } from "./video.js";

export var fitMode = "contain";

export function setFitMode(v) { fitMode = v; }

export function applyFit() {
  var zb = $("zoombox");
  zb.classList.remove("fit-width", "fit-full");
  if (fitMode === "width") zb.classList.add("fit-width");
  else if (fitMode === "full") zb.classList.add("fit-full");
  // 画面尺寸变化后重置视图变换并通知画布重算
  resetZoom();
  if (vmode === "blocks") fitCanvas();
}
