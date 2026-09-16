/* 登录：口令不再留在 URL，HttpOnly Cookie 会话；登录后抹掉地址栏参数。 */
import { $, relay, deviceId, setRelay, setDeviceId } from "./state.js";
import { fetchInfo } from "./info.js";

var authBox = $("auth");

export function cleanUrl() {
  // 登录成功后把口令/识别码口令从地址栏抹掉，防止转发链接泄漏
  var q = relay && deviceId ? "?id=" + encodeURIComponent(deviceId) : "";
  history.replaceState({}, "", location.pathname + q);
}
function showAuth() {
  if (relay) {
    $("auth-title").textContent = "需要识别码与口令";
    $("auth-desc").textContent = "在电脑上运行 server.py（识别码模式）时，终端会打印识别码和口令。登录后口令不会出现在网址里。";
    $("auth-input-id").hidden = false;
    $("auth-input").placeholder = "口令";
    if (deviceId) $("auth-input-id").value = deviceId;
  } else {
    $("auth-title").textContent = "需要访问口令";
    $("auth-desc").textContent = "在电脑上运行 server.py 时，终端会打印带 token 的访问链接。登录后口令不会出现在网址里。";
    $("auth-input-id").hidden = true;
    $("auth-input").placeholder = "口令 / token";
  }
  authBox.hidden = false;
}
function doLogin() {
  var body = new URLSearchParams();
  if (relay) {
    var id = $("auth-input-id").value.trim() || deviceId;
    if (!id) { $("auth-input-id").focus(); return; }
    setDeviceId(id);
    body.set("id", id);
    body.set("pass", $("auth-input").value.trim());
  } else {
    body.set("token", $("auth-input").value.trim());
  }
  $("auth-desc").textContent = "正在验证…";
  fetch("/login", { method: "POST", body: body, credentials: "same-origin" })
    .then(function (r) {
      if (r.status === 403) $("auth-desc").textContent = "口令/识别码错误，请重试";
      else if (r.status === 429) $("auth-desc").textContent = "尝试过于频繁，请 5 分钟后再试";
      else { cleanUrl(); location.reload(); }
    })
    .catch(function () { $("auth-desc").textContent = "网络错误，请重试"; });
}
$("auth-go").addEventListener("click", doLogin);
$("auth-input").addEventListener("keydown", function (e) { if (e.key === "Enter") doLogin(); });

export { showAuth };
