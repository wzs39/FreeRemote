/* 文件互传面板：目录浏览、上传、下载（File System Access 流式 / Blob 兜底）。 */
import { $, apiUrl, el } from "./state.js";

var filePanel = $("filepanel");
var fpPath = "";

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function fmtSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB";
  return (n / 1073741824).toFixed(2) + " GB";
}
function parentOf(p) {
  p = String(p).replace(/\\/g, "/");
  var i = p.lastIndexOf("/");
  if (i <= 0) return "";
  return p.slice(0, i);
}
function toggleFiles(show) {
  filePanel.classList.toggle("show", show);
  if (show) loadFiles();
}
function fpSetProgress(pct, name) {
  $("fp-progress").hidden = false;
  $("fp-bar").firstChild.style.width = pct + "%";
  $("fp-pct").textContent = pct + "%";
  $("fp-name").textContent = name || "";
}
function loadFiles() {
  fetch(apiUrl("/files", "&path=" + encodeURIComponent(fpPath || "")))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var list = $("fp-list");
      list.innerHTML = "";
      if (!d.ok) { list.appendChild(el("div", "fp-err", d.error || "加载失败")); return; }
      fpPath = d.path;
      $("fp-path").textContent = d.path;
      $("fp-up").disabled = !d.parent;
      d.entries.forEach(function (e) {
        var row = document.createElement("div");
        row.className = "fp-row";
        row.innerHTML = "<span>" + (e.is_dir ? "📁" : "📄") + " " + escapeHtml(e.name) + "</span>"
          + (e.is_dir ? "" : '<span class="fp-size">' + fmtSize(e.size) + "</span>");
        row.addEventListener("click", function () {
          if (e.is_dir) { fpPath = d.path + "/" + e.name; loadFiles(); }
          else downloadFile(d.path, e.name);
        });
        list.appendChild(row);
      });
      if (!d.entries.length) list.appendChild(el("div", "fp-msg", "（空目录）"));
    })
    .catch(function () {
      var list = $("fp-list"); list.innerHTML = "";
      list.appendChild(el("div", "fp-err", "无法连接，请确认设备在线"));
    });
}
function downloadFile(dir, name) {
  var url = apiUrl("/download", "&path=" + encodeURIComponent(dir + "/" + name));
  // 支持 File System Access API（Android Chrome 等）：流式写入磁盘，大文件不占内存
  if (window.showSaveFilePicker) {
    (async function () {
      try {
        var handle = await window.showSaveFilePicker({ suggestedName: name });
        var writable = await handle.createWritable();
        var resp = await fetch(url, { credentials: "same-origin" });
        if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
        var total = parseInt(resp.headers.get("Content-Length") || "0", 10) || 0;
        var reader = resp.body.getReader();
        var received = 0;
        fpSetProgress(0, name);
        while (true) {
          var chunk = await reader.read();
          if (chunk.done) break;
          await writable.write(chunk.value);
          received += chunk.value.length;
          if (total) fpSetProgress(Math.round(received / total * 100), name);
        }
        await writable.close();
        $("fp-progress").hidden = true;
        $("fp-name").textContent = "已保存";
      } catch (e) {
        if (e && e.name === "AbortError") { $("fp-progress").hidden = true; return; }
        $("fp-name").textContent = "下载失败：" + (e.message || "");
        $("fp-progress").hidden = true;
      }
    })();
    return;
  }
  // 兜底：Blob 方式（iOS Safari 等）
  var xhr = new XMLHttpRequest();
  xhr.open("GET", url);
  xhr.responseType = "blob";
  fpSetProgress(0, name);
  xhr.onprogress = function (e) {
    if (e.lengthComputable) fpSetProgress(Math.round(e.loaded / e.total * 100), name);
  };
  xhr.onload = function () {
    if (xhr.status === 200) {
      var a = document.createElement("a");
      a.href = URL.createObjectURL(xhr.response);
      a.download = name;
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
      $("fp-progress").hidden = true;
    } else {
      fpSetProgress(0, "");
      $("fp-name").textContent = "下载失败 (" + xhr.status + ")";
    }
  };
  xhr.onerror = function () { $("fp-name").textContent = "下载失败"; };
  xhr.send();
}
function uploadFiles() {
  var input = $("fp-input");
  var f = input.files && input.files[0];
  if (!f) return;
  var xhr = new XMLHttpRequest();
  xhr.open("POST", apiUrl("/upload", "&path=" + encodeURIComponent(fpPath || "") + "&name=" + encodeURIComponent(f.name)));
  fpSetProgress(0, f.name);
  xhr.upload.onprogress = function (e) {
    if (e.lengthComputable) fpSetProgress(Math.round(e.loaded / e.total * 100), f.name);
  };
  xhr.onload = function () {
    if (xhr.status === 200) {
      $("fp-progress").hidden = true;
      input.value = "";
      loadFiles();
    } else {
      fpSetProgress(0, "");
      $("fp-name").textContent = "上传失败 (" + xhr.status + ")";
    }
  };
  xhr.onerror = function () { $("fp-name").textContent = "上传失败"; };
  xhr.send(f);
}

$("b-files").addEventListener("click", function () {
  toggleFiles(!filePanel.classList.contains("show"));
});
$("fp-close").addEventListener("click", function () { toggleFiles(false); });
$("fp-up").addEventListener("click", function () {
  fpPath = parentOf($("fp-path").textContent);
  loadFiles();
});
$("fp-send").addEventListener("click", uploadFiles);
