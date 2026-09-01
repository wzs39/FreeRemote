# FreeRemote —— 手机远程控制电脑（监看 + 控制）

一个自建的、轻量的"手机 → 电脑"远程控制程序：

- **监看**：电脑屏幕实时推流到手机浏览器（分块增量刷新，延迟低、静态画面零流量；老浏览器自动降级 MJPEG）
- **控制**：触摸单击/长按/拖动/双指滚动，虚拟键盘输入文字，Ctrl/Alt/Shift/Win 修饰键
- **跨平台**：服务端支持 Windows / macOS / Linux，手机端任何带浏览器的设备（Android / iPhone / iPad）
- **无需公网服务器**：局域网内即开即用；外网访问可配合 Tailscale / SakuraFrp / 自建中继（见下文）
- **远程唤醒**：设备离线时手机一键发 WoL 开机（中继与电脑同局域网即可）
- **扫码登录**：电脑终端打印二维码，手机相机一扫即登录，免输识别码
- **PWA**：手机可“添加到主屏幕”像 App 一样用，监看时屏幕常亮

> 🚀 **从零安装到熟练使用？请看 [`docs/INSTALL_AND_USE.md`](docs/INSTALL_AND_USE.md)（安装+使用完整教程）**——
> 电脑端安装、局域网/跨网络远程、开机自启+远程唤醒、手机端手势/键盘/文件/自检、排障。
>
> 📖 需要更细的操作说明？见 [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md)（用户使用手册）——
> 涵盖快速上手、手机连接登录、触摸手势、虚拟键盘、画质、文件互传、识别码模式、
> 自检自愈、安全建议、命令行参数速查与常见问题 FAQ。
> 免费部署中继：见 [`docs/DEPLOY_RELAY.md`](docs/DEPLOY_RELAY.md)。
> 用自己的服务器 Docker 部署中继：见 [`docs/DEPLOY_DOCKER.md`](docs/DEPLOY_DOCKER.md)。
> 🥇 推荐：手机+电脑 Tailscale 组网（无需公网 IP / 中继服务器，跨网络直连）：见 [`docs/DEPLOY_TAILSCALE.md`](docs/DEPLOY_TAILSCALE.md)。

---

## 一、已有方案调研（先看这个）

网上现成的"手机远程控制电脑"方案很多，按"是否可自建/开源"分类：

| 方案 | 开源/自建 | 手机 App | 特点 | 适合场景 |
|---|---|---|---|---|
| **RustDesk** | ✅ 开源，可自建中继服务器 | Android/iOS/Web | 功能最全、可自托管、免费；GitHub 32k+ star | **生产/长期使用首选**，直接下载客户端即用 |
| **Chrome Remote Desktop** | ❌ 免费但闭源 | Android/iOS | 免安装、稳定，但绑定 Google 账号、依赖其服务器 | 不想折腾、接受 Google |
| **Tailscale + Windows RDP** | ✅ 开源（Tailscale 客户端免费） | Microsoft 远程桌面 App | 安全隧道 + 原生 RDP，体验好；适合 Windows | 已有 Tailscale 生态的人 |
| **VNC + 手机客户端**（RealVNC / RemoteToGo 等） | VNC 服务端部分开源 | 需另装客户端 | 老牌方案，配置略繁琐，性能一般 | 兼容旧设备 |
| **本方案（浏览器方案）** | ✅ 全部自建 | 无需 App，浏览器直接打开 | 极轻量、零安装、代码完全可控 | 想在自有代码上扩展 / 快速临时用 |

> 调研结论：如果只是"想用"，直接用 **RustDesk**（或自建其服务器）最省心；本项目的价值在于**完全自建、可改代码**，并演示了"屏幕采集推流 + WebSocket 注入输入"这一套远程控制的核心原理，可在此基础上二次开发。

---

## 二、快速开始

### Windows

双击 `run.bat`，或命令行运行：

```bat
run.bat
```

### macOS / Linux

```bash
chmod +x run.sh
./run.sh
```

脚本会自动创建虚拟环境并安装依赖。也可以手动：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt   # Windows: .venv\Scripts\python.exe -m pip ...
.venv/bin/python server.py                             # Windows: .venv\Scripts\python.exe server.py
```

### 手机连接

1. 电脑和手机连**同一个 WiFi / 局域网**；
2. 看电脑终端打印的链接，例如 `http://192.168.1.23:8080/?token=AbCdEf`；
3. 手机浏览器（Safari / Chrome 均可）打开该链接，输入口令登录即可（**口令登录后不会留在网址里**）。
4. 旧版带 `?token=` 的链接仍然兼容，打开后会自动登录并清理地址栏。

> **升级/重启提示**：页面是禁止缓存的（每次打开都是最新客户端，顶栏有版本号可核对）；
> 重复启动会互相冲突，重启前先运行 `stop.bat`（Windows）或 `./stop.sh`（macOS/Linux）
> 停止所有旧进程，再重新 `run.bat`。

> Windows 首次运行如果弹出防火墙提示，点「允许访问」（仅限专用网络）；如果没弹，可手动放行：
> ```bat
> netsh advfirewall firewall add rule name="FreeRemote" dir=in action=allow protocol=TCP localport=8080
> ```

---

## 三、使用说明

| 操作 | 手势 / 按钮 |
|---|---|
| 移动鼠标 | 单指拖动 |
| 左键单击 | 轻点屏幕 |
| 右键单击 | 长按屏幕（0.5 秒） |
| 滚轮 | 双指上下滑动 |
| 中键 / 左右键 / 滚轮按钮 | 底部工具栏 |
| 文字输入 | 「⌨ 键盘」→ 英文/符号直接打字，中文粘贴发送（发送后自动聚焦可连打） |
| 文件互传 | 顶栏「📁 文件」→ 浏览电脑目录 → 点文件下载 / 选文件上传（带进度条） |
| 组合键 | 先点 Ctrl/Alt/Shift/Win 再操作；「CtrlAltDel」按钮 |
| 画质切换 | 顶栏 流畅 / 均衡 / 高清（低画质适合弱网） |
| 全屏 | 「全屏」按钮 |
| 横屏布局 | 竖屏顶部有「↻ 建议横屏」提示；横屏时控制集成到右侧面板、按钮更大更顺手 |

### 命令行参数

```bash
python server.py --port 8080 --fps 10 --preset mid --monitor 1 --token MyPass
```

- `--port`：监听端口，默认 8080
- `--fps`：推流帧率 1–30，默认 8（帧率越高越流畅，越费带宽）
- `--preset`：默认画质 `low` / `mid` / `high`
- `--monitor`：显示器编号，1 = 主屏，可查看 `python server.py --monitor 2` 尝试扩展屏
- `--token`：访问口令（局域网模式），默认每次随机生成；**推荐用 `renew-token.bat` 一键生成带有效期的固定口令**（默认 7 天）
- `--renew-token [天数]`：重新生成 token.txt 口令并设置有效期（默认 7 天；`0`=永久）
- `--once`：一次性口令模式（10 分钟有效，临时分享用）
- `--no-auth`：关闭口令校验（仅限可信局域网）
- `--allow-ips`：IP 白名单，逗号分隔（如 `192.168.1.23,10.0.0.5`），其余 IP 一律拒绝
- `--tls-cert` / `--tls-key`：TLS 证书与私钥路径，同时给出即启用 HTTPS
- `--relay`：识别码模式：中继服务器地址，如 `wss://你的域名` 或 `ws://1.2.3.4:9090`
- `--id`：识别码模式：设备识别码（默认随机 6 位数字）
- `--password`：识别码模式：设备口令（默认随机生成）

---

## 四、自检、自我审查与补救

系统内置三层自检/自愈能力，问题可被提前发现并自动补救：

### 1. 命令行自检工具（电脑端，一键体检）

```bash
python doctor.py                       # Windows: doctor.bat；macOS/Linux: ./doctor.sh
python doctor.py --port 8080           # 顺带检查端口是否空闲
python doctor.py --relay wss://你的域名 --id ABC123 --password xxx   # 检查中继连通性+识别码配对
python doctor.py --test-input          # 实际测试鼠标注入（鼠标会移动 1 像素）
```

逐项检查并输出 ✅/⚠️/❌ 报告，每一项问题都附带**补救方案**：

| 检查项 | 发现的问题示例 | 补救方案 |
|---|---|---|
| Python/依赖 | 缺 Pillow | `pip install -r requirements.txt` |
| 屏幕采集 | macOS 未授权 | 系统设置→隐私与安全性→屏幕录制 授权 |
| 屏幕状态 | 检测到锁屏 | 解锁电脑后再操作 |
| 鼠标注入 | macOS 辅助功能未授权 | 系统设置→隐私与安全性→辅助功能 授权 |
| 端口 | 8080 被占用 | 换 `--port`，或结束占用进程 |
| 中继 | 中继不可达 / 口令错误 | 核对地址、`--devices` 登记、HTTPS 用 `wss://` |
| 安全 | 口令过短 / 关闭了鉴权 | 使用 ≥8 位强口令 |

### 2. 运行时自愈（无需人工干预）

- 屏幕采集**连续失败** → 自动重建采集器
- 实际帧率**低于目标一半** → 自动降一档画质（可在手机端手动切回）
- 中继断线 → 5 秒自动重连
- 所有自动补救都会**记录日志**，可在手机端自检页查看

### 3. 手机端自检仪表盘

主界面顶栏点「🩺 自检」打开 `/doctor` 页面（局域网与识别码模式通用）：

- 总体状态 ✅/⚠️/⏳ + 运行时长、实际帧率、画质、分辨率、重连次数
- 各项健康检查明细，未通过项直接给出补救建议
- 「警告与建议」「自动补救记录」两个面板，每 3 秒自动刷新

> 中继模式下，电脑端每 5 秒把自检数据上报给中继，手机端自检页显示的是**电脑的真实健康状态**。

---

## 五、远距离控制（手机与电脑在不同网络，通过识别码精准配对）

局域网模式只适合同一 WiFi。要“人在外面控制家里的电脑”，使用 **识别码 + 中继服务器** 模式
（和 RustDesk / TeamViewer 同款架构）：

```
电脑 ──外连──▶ 中继服务器(公网) ◀──外连── 手机浏览器
     推帧上行 / 收指令下行          识别码精准配对
```

- 电脑**主动外连**中继，无需公网 IP、无需路由器端口转发
- 手机打开中继网址，输入**识别码 + 口令**即可精准找到并控制那台电脑
- 两端都支持多设备：一台电脑可被多个手机同时监看

### 第 1 步：部署中继服务器（任选一种）

1. **任意 VPS / 云主机**：
   ```bash
   python3 relay.py --port 9090
   # 可选：预注册固定识别码，禁止自动注册
   python3 relay.py --port 9090 --devices "ABC123:强口令" --strict
   ```
2. **免费托管平台**（Render / Fly.io 等）：**详细到每一步的教程见 `docs/DEPLOY_RELAY.md`** ——
   含 Dockerfile / `render.yaml` / `fly.toml` 部署文件、免绑定卡的免费额度、**HTTPS 与 wss 自动配置**、`--xf-trusted` 真实 IP 说明、常驻与休眠、大陆访问与排障。基本流程：
   - Render：网页 `New → Blueprint` 关联仓库（自动读 `render.yaml`），完成后得到 `https://<名>.onrender.com` 就绪；
   - Fly.io：`fly launch → fly deploy → fly certs create <应用>.fly.dev`；
   - 部署后电脑端用 `python server.py --relay wss://<你的域名> --id 识别码 --password 强口令`。
3. 推荐用 **Caddy / Cloudflare** 加 HTTPS，这样手机端自动走 `wss`（更安全）。
   > 若在平台（Render/Fly/Caddy 等）后方部署，中继请加 `--xf-trusted`，
   > 让限速/审计拿到手机真实 IP（见 `docs/DEPLOY_RELAY.md` 第 4 节）。

**中继的监控与安全参数**：

```bash
python3 relay.py --port 9090 [--conn-policy replace|reject] [--hb-timeout 90] [--hb-interval 10]
```

- **设备心跳监控**：电脑端每 5 秒上报遥测，中继据此判断存活；任一通道静默超过
  `--hb-timeout`（默认 90 秒）判定断线，强制下线并触发电脑端自动重连（不再让死连接占位）。
  可调小到 `--hb-timeout 20` 更快发现电脑离线。
- **同识别码多端连接策略** `--conn-policy`：
  - `replace`（默认）：新电脑连接时**顶替旧连接**（换机/断线重连自动接管），并记录「设备被顶替」告警；
  - `reject`：设备已在线就**拒绝新连接**（HTTP 409），防止两台电脑争抢同一识别码。
- **会话日志**：每一次设备/手机会话的起止与时长写入 `sessions.log`（含识别码、来源 IP、
  控制/监看通道），与安全审计 `security.log` 互补，方便排查“谁什么时候连过哪台设备”。
- **`/devices` 端点**：登录后列出当前所有在线设备的心跳/存活/时长/分辨率/画质/会话统计，运维排查一目了然。

### 第 2 步：电脑端连接中继

```bash
python server.py --relay wss://你的域名 --id ABC123 --password 强口令
# 不指定 --id / --password 时自动随机生成并在终端打印
```

### 第 3 步：手机端连接

打开 `https://你的域名/`，输入识别码和口令；或直接打开带参数的链接：
`https://你的域名/?id=ABC123&pass=强口令`。页面会显示设备在线状态，设备离线时自动重连。

> **备选方案一（Tailscale，最安全）**：电脑和手机都装 **Tailscale**（免费，WireGuard 端到端加密）并登录同一账号，
> 手机直接打开 `http://<电脑的TailscaleIP>:8080/` 输入口令登录，任何网络（4G/5G/别家 WiFi）都能连。
> 参考：https://tailscale.com/blog/tailscale-rustdesk-remote-desktop-access
>
> **备选方案二（SakuraFrp 内网穿透，手机零安装）**：在有 natfrp.com 账号的前提下：
> 1. 电脑端 `python server.py --port 8080 --token 强口令`；
> 2. SakuraFrp 控制台创建**HTTP 隧道**，本地指向 `127.0.0.1:8080`（用 HTTP 类型而非 TCP，
>    这样 frpc 会把手机真实 IP 写入 X-Forwarded-For，限速/审计按真实 IP 生效）；
> 3. 手机打开隧道网址（如 `https://xxx.natfrp.cloud`），输入口令登录即可监看+控制+文件互传。
> 注意：HTTP 隧道明文传输，建议用 SakuraFrp 的 HTTPS 隧道或电脑端 `--tls-cert/--tls-key` 自启 HTTPS；
> 口令务必 ≥12 位。SakuraFrp 只是端口转发，无法运行 relay.py，所以识别码中继模式仍需要一台公网机器。

> ⚠️ 安全提醒：中继口令请用强密码，并务必给中继加 HTTPS。不要把局域网模式的 8080 端口
> 直接映射到公网，否则任何人扫到都可能尝试连接。

---

## 六、文件互传（手机 ↔ 电脑）

顶栏点「📁 文件」打开文件面板：

- **电脑 → 手机（下载）**：浏览目录，点文件名即下载到手机（支持目录逐级进入、返回上级）
- **手机 → 电脑（上传）**：选文件 → 上传，实时进度条；上传后自动刷新列表
- 支持**大文件**（分块流式传输，不经内存整载）、中文/特殊字符文件名、目录内排序显示

**两种模式都支持**：

- 局域网模式：手机直接连电脑的 `/files` `/upload` `/download` 接口
- 识别码模式：中继服务器转发，电脑与中继之间走**独立的文件专用通道**（`/pc/file`），
  与屏幕推流、控制指令互不干扰

共享目录默认是电脑的**下载文件夹**，可用 `--share-dir` 指定：

```bash
python server.py --share-dir "D:\共享"        # 局域网模式
python server.py --relay wss://你的域名 --share-dir "D:\共享"   # 识别码模式
```

> ⚠️ 安全提示：文件功能与远程控制共用同一套鉴权（token / 识别码+口令）。
> 文件面板可浏览从共享目录开始的目录树，请勿把口令外泄；公网中继务必加 HTTPS 并用强口令。

---

## 七、安全防护（防入侵）

默认打开页面后输入口令登录，**口令不再出现在网址里**，转发链接也不会泄漏口令。
整套系统带多层防护：

### 1. 会话登录（口令不进 URL）

- 登录成功后签发 **HttpOnly + SameSite=Lax 的会话 Cookie**（24 小时有效），口令只存在于服务端会话与登录请求体内；
- 手机端登录成功后自动用 `history.replaceState` **把口令从地址栏抹掉**（中继模式仅保留非敏感的识别码）；
- 旧版 `?token=` / `?id=&pass=` 链接自动兼容，登录后即清理。
- Cookie 对 WebSocket 通道同样生效（浏览器握手自动携带），控制/推流/文件全部走统一鉴权。

### 2. 防暴力破解限速

- 同一 IP 登录失败超过 5 次，临时锁定 5 分钟（返回 429）；
- 每次失败都计入审计日志，可据此发现攻击者。

### 3. IP 白名单（局域网可选）

```bash
python server.py --port 8080 --token xxx --allow-ips 192.168.1.23
```

白名单之外的 IP 即使口令正确也一律 403（先于口令校验）。适合固定手机/固定办公室的场景。

### 4. 可选 TLS（HTTPS）

```bash
python server.py --port 8443 --token xxx --tls-cert cert.pem --tls-key key.pem
```

启用后手机端走 `https://`（WebSocket 自动变 `wss://`），局域网防嗅探。
证书可用 Caddy 免费签发，或 `openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=你的IP"` 自签（自签证书手机端需信任）。

### 5. 安全审计日志

- 所有**登录成功/失败、限速拒绝、IP 白名单拒绝、文件上传/下载/浏览、控制连接建立/断开**都写入 `security.log`；
- 中继服务器额外记录**设备注册 / 上线 / 下线**告警（可发现陌生设备接入）；
- 最近 50 条事件可通过 `/events` 接口在手机端查看（需登录），方便入侵排查。
- 中继额外提供**会话日志** `sessions.log`（设备/手机每次会话留痕）和**设备心跳监控**
  （超时自动下线重连），配合 `/devices` 端点可实时掌握所有设备的在线与存活状态。
- 运行日志统一分级：`logs/server.log` / `logs/relay.log` 每行带 `[时间] [级别]`（INFO/WARN/ERROR），
  **查错误一条命令：`grep "[ERROR]" logs/server.log`（或 logs/relay.log）**，详见使用手册「13. 日志与排查」。

### 6. 其它基线

- 口令默认随机生成且长度 ≥ 8 位；自检工具会提示弱口令；
- 页面禁止缓存（`no-store`），防止旧版/敏感页面残留；
- 公网部署中继时：务必 HTTPS + 强口令；`--devices` 预注册 + `--strict` 可禁止陌生设备自动注册。

---

## 八、工作原理与限制

### 原理

```
电脑端：mss 采集屏幕 → 分块差异编码（只传变化区域）──▶ 手机 canvas 增量渲染（监看）
        pyautogui 注入鼠标/键盘/滚动 ◀──WebSocket 指令── 手机手势/虚拟键盘（控制）
        光标位置随帧消息下发，手机端本地绘制（mss 不采集光标）
```

- **默认分块增量推流（/vstream）**：屏幕切成 64×64 小块，逐帧对比只编码**变化区域**的 JPEG，
  静态画面零流量；变化超过 60% 时自动改发全帧。配合客户端 canvas 渲染，局域网延迟约 0.1~0.2s，
  移动网络下带宽也大幅下降。光标由手机端绘制，不再依赖画面里出现光标。
- **MJPEG 兜底（/stream）**：老浏览器或推流失败时自动降级为 multipart/x-mixed-replace 的 `<img>` 播放。
- `/ws`（局域网）/ `/ctl`（中继）是控制指令通道；`/info` 返回分辨率/画质；`/status` 返回自检数据。
- 分块采集为**懒启动**（有客户端才采集），与 MJPEG 兜底互不重复采集；
  多手机同时监看时共享同一份采集结果。

### 已知限制

- 电脑**锁屏 / 无显示器会话**时无法控制（与 TeamViewer 等远程软件相同；Windows 睡眠时需先唤醒）。
- 手机在弱网/移动网络下建议用「流畅」画质。
- 文字输入采用"粘贴"方式，会占用系统剪贴板。
- 键位映射为常规键名，特殊布局（如德语键盘）需自行调整。
- macOS 首次使用需在「系统设置 → 隐私与安全性」中授予**屏幕录制**（mss 采集）与**辅助功能**（pyautogui 注入）权限。

### 目录结构

```
server.py          电脑端：采集/推流/指令注入 + 文件互传 + 运行时自检自愈（局域网 + 识别码中继模式）
relay.py           中继服务器：识别码配对，跨网络转发帧/指令/文件 + 心跳监控 + 会话日志 + 多端连接策略（部署到公网）
doctor.py          自检工具：环境/采集/注入/网络/安全体检 + 补救方案
web/index.html     手机端网页：登录 + 监看 + 手势控制 + 虚拟键盘 + 文件互传（两种模式通用）
web/doctor.html    手机端自检仪表盘（/doctor）
run.bat / run.sh   一键启动脚本（Windows / macOS·Linux，含防重复启动检查）
stop.bat / stop.sh 一键停止所有 FreeRemote 进程（清理旧客户端遗留进程）
doctor.bat / doctor.sh  自检快捷脚本
logs/              运行日志目录：server.log / relay.log / security.log / sessions.log（自动生成，均已 gitignore）
requirements.txt   依赖清单
```

---

## 九、二次开发方向

- 把 `/stream` 换成 WebRTC（更低延迟，但实现更复杂）
- 增加文件传输（拖拽上传到电脑）
- 音频转发（监看声音）
- 手机端屏幕方向自动旋转适配
- 会话录制 / 回放
