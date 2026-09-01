# 把 FreeRemote 中继免费部署到 Render / Fly.io（含 HTTPS / wss）

本文档教你**完全免费**把 `relay.py`（识别码中继）部署到公网，让手机和电脑在**不同网络（不同 WiFi / 手机流量）**下都能互相远程控制，并配好 **HTTPS / wss**。

> **先明确一个概念**：本项目中继跑在公网，手机与电脑都主动外连它来交换「屏幕帧 + 控制指令」。它本身就是一台 Web 服务，同时提供网页（`/`）、API（`/info /status`）和 WebSocket（`/pc/ws /ctl /vstream`）。下面的部署就是把这台服务挂到公网上，并让它带 HTTPS。

---

## 0. 两种平台怎么选

| | **Render** | **Fly.io** |
|---|---|---|
| 上手难度 | 低（网页点几下，无需本地命令行） | 中（要装 CLI、建 git、跑命令） |
| 免费额度 | 750 小时/月（够 1 台常驻）、512MB、5GB 流量 | 约 1 台 `shared-cpu-1x / 256MB` 常驻，包含在免费额度内（约 $0~2/月） |
| 常驻问题 | ⚠️ **空闲 15 分钟会休眠**（电脑端在线时靠遥测可保持唤醒） | ✅ 配置后**可 24h 常驻不休眠** |
| HTTPS/wss | ✅ `*.onrender.com` 自动免费 HTTPS | ✅ `*.fly.dev` 免费 Let's Encrypt 证书 |
| 支付 | 无需绑卡 | 需要绑信用卡/借记卡（免费额度内不扣费） |
| 中国大陆访问 | ⚠️ render.com 偶发访问不稳 / 被墙 | ⚠️ fly.dev 也非大陆优化 |

**结论**：
- 想要「**常驻、最像正式服务器**」→ 选 **Fly.io**（配置好不会休眠）。
- 想要「**最快跑通、不用装任何东西**」→ 选 **Render**（你的电脑开机且连着中继时，它会一直醒着；电脑关机后手机第一次访问会等约 30~60s 冷启动）。
- 身处**中国大陆**：这两个平台都不保障稳定直连。优先选离你近的区域（Render `singapore`/`hongkong`；Fly `sin`/`hkg`/`nrt`）。如果实测连不上，参考文末「替代方案」。

---

## 1. 准备：把这几份文件提交到 Git

本仓库已经在根目录放好了部署需要的文件：

| 文件 | 作用 |
|---|---|
| `requirements-relay.txt` | 只装 `aiohttp` + `Pillow`（**别用根目录 requirements.txt**，里面有 pyautogui 等电脑端依赖，Linux 无桌面会装失败） |
| `Dockerfile` | 构建镜像：装依赖 → 复制 `relay.py` + `web/` → 启动（已带 `--xf-trusted`） |
| `render.yaml` | Render「蓝图」一键部署配置 |
| `fly.toml` | Fly.io 部署配置（已设常驻/强制 HTTPS） |
| `.dockerignore` | 别把 `.venv` 等本地文件打进镜像 |

新建一个 git 仓库并提交（两个平台都需要）：

```bash
cd 项目目录
git init
git add -A
git commit -m "FreeRemote 中继"
```

> **首次部署前建议在本地先验证一次**（可选但推荐，能省很多排障）：
> ```bash
> docker build -t freeremote-relay .
> docker run --rm -p 9090:8080 freeremote-relay
> # 新终端验证：
> curl http://127.0.0.1:9090/mode          # 应返回 {"mode":"relay"}
> ```

---

## 2. 部署到 Render（网页点几下，最快）

1. 注册 https://render.com → 用 GitHub 登录（会授权访问你的仓库）。
2. 去 https://dashboard.render.com → **New** → **Blueprint**。
3. **关联你的 GitHub 仓库**，Render 会自动读取仓库根目录的 `render.yaml`。
4. 点 **Apply** → **Deploy**。等约 2~5 分钟构建完成。
5. 完成后在服务页能看到你的域名，例如 `https://freeremote-relay.onrender.com`。

**HTTPS / wss 自动就绪**：Render 给 `*.onrender.com` 免费签发 HTTPS 证书，手机端打开 `https://...onrender.com` 登录即可，`wss://` 由页面自动切换（无需你在容器里配 TLS）。

> 想让电脑关机后手机也能秒连，就不能让服务休眠。免费版可这样缓解：
> 在服务页 **Settings → 挂一个外部 HTTP 定时探活**（或用 `cron-job.org` / UptimeRobot 每 12 分钟 GET 一次 `/mode`）。不过免费额度 750h/月刚好够 1 台常驻，探活会略微增加流量，够用。

---

## 3. 部署到 Fly.io（CLI，可常驻）

前置：装 [flyctl](https://fly.io/docs/flyctl/)，并准备一个 git 仓库（见第 1 节），一个 GitHub/GitLab 账号，一张用于风控的信用卡（免费额度内不扣费）。

```bash
fly auth login            # 浏览器登录
cd 项目目录（已 git init）
fly launch --name freeremote-relay --region hkg --no-deploy
```
- `--name` 应用名全局唯一，改成你喜欢的；`--region` 选最近的：`hkg`(香港) `sin`(新加坡) `nrt`(东京) `sjc`(美国)。
- `--no-deploy`：先只建配置不部署。它会生成 `fly.toml`（已有则沿用，确认内容与仓库内的 `fly.toml` 一致，重点是 `auto_stop_machines = false` 和 `min_machines_running = 1`）。

然后部署并配 HTTPS：

```bash
fly deploy                       # 用 Dockerfile 构建并上线
fly certs create freeremote-relay.fly.dev   # 申请免费 Let's Encrypt 证书
```
`fly certs create <应用名>.fly.dev` 通常几秒到几分钟签发完成，签发后 `https://freeremote-relay.fly.dev` 即为你的中继地址。

常用运维命令：

```bash
fly status                       # 机器状态
fly logs                         # 实时看日志（登录/会话/心跳/错误都在这）
fly certs show freeremote-relay.fly.dev   # 看证书状态
```

---

## 4. HTTPS / wss 到底怎么配的？（原理，重要）

**不需要在容器里配 TLS。** TLS 由平台（Render / Fly / Caddy）在**边缘网关**终结，转发给容器内 `8080` 的纯 HTTP 服务：

```
手机浏览器 ──https://x.onrender.com──▶ [Render 边缘: TLS终结] ──http:8080──▶ relay.py
手机 WS    ──wss://x.onrender.com──▶ [Render 边缘]            ──ws:8080──▶ relay.py
电脑 WS    ──wss://x.onrender.com/pc/ws──▶ [边缘]              ──ws──▶ relay.py
```

因此你只需要在**两端用 `wss://` / `https://`** 拼地址，其余自动。我们的代码已做好配套：

- 手机端网页根据 `location.protocol === "https:"` 自动把 WebSocket 切到 `wss://`（无需改）。
- 电脑端（`server.py --relay wss://...`）用 `wss://` 外连即可，Let's Encrypt 证书自带信任链，Python 直接可连。
- **`--xf-trusted`**：平台把真实用户 IP 写在 `X-Forwarded-For` 头里（代码里已实现读取最后一个 IP），这样约束与审计拿到的是**手机真实 IP** 而不是代理 IP。Render 部署时在 `startCommand`、Fly 在 `Dockerfile` 里都已带上该开关。

---

## 5. 电脑端连接（识别码模式）

在任何一台想被控制的电脑上运行：

```bash
# Windows：.venv\Scripts\python.exe server.py --relay wss://<你的域名> ...
# macOS/Linux：.venv/bin/python server.py ...

python server.py --relay wss://freeremote-relay.onrender.com \
                 --id ABC123 --password 一个足够长的强口令
```

- 不指定 `--id/--password` 会自动生成并在终端打印。
- 连上后中继会打印 `设备注册 / 设备上线` 的审计；电脑端日志出现 `[已连接中继]` 即成功。
- **给多台电脑分配不同识别码**即可用同一中继管多台设备。

---

## 6. 手机端连接

手机浏览器（任何网络，哪怕是 4G/5G/别家 WiFi）打开：

```
https://freeremote-relay.onrender.com/
```

输入电脑端**识别码 + 口令**登录 → 即可**监看 + 触摸控制 + 虚拟键盘 + 文件互传**。状态栏会显示「识别码 xxx · 在线」。

> 口令登录后不会留在网址里；想分享给家人就发纯网址（无 `?pass=`），让对方自己输口令。

---

## 7. 安全建议（部署到公网后必做）

```bash
# 推荐：只允许预注册的识别码，禁止陌生人自动注册 + 强口令
python server.py --relay wss://<你的域名> --id ABC123 --password '<16位以上强口令>'

# 中继端：并行预注册 + 严格模式（可选）
python relay.py --port 8080 --devices "ABC123:<强口令>" --strict --xf-trusted
```

- **务必用长强口令**：公网上任何人扫到都能试登录。我们的**防暴力破解限速（5次失败锁5分钟）**和 **`security.log` 审计**会兜底并留痕。
- 想到可疑动静？中继日志会记：**登录成功/失败、设备注册/上线/下线、被顶替、心跳超时**；`/events` 接口可在线查看最近事件，`/devices` 可看当前所有在线设备的心跳。
- 两台电脑不小心用了同一识别码：默认 `replace` 策略会新旧顶替并留「设备被顶替」告警；想严格要求一码一机改用 `--conn-policy reject`。

---

## 8. 排障

| 现象 | 原因与处理 |
|---|---|
| 手机打开域名很慢/首次等很久 | 免费实例冷启动；Render 空闲休眠后首次访问要等。常驻走 Fly |
| 电脑端一直 `[中继断开] 5 秒后重连` | 识别码/口令错（核对）、域名证书未签好（Fly 等 `fly certs`）、或没带 `wss://`（`ws://` 会被边缘 443 拒绝） |
| 状态栏「设备离线」 | 电脑没连中继 / 电脑关机断网。电脑连上后手机端 3 秒内自动恢复 |
| 限速/审计里 IP 全是同一个 | 部署时漏了 `--xf-trusted`（trusted 分支拿的是代理 IP）|
| 会话日志在哪 | 中继把 `sessions.log`、`security.log` 写在 `logs/` 目录，平台把它当作 stdout 之外的文件，**查这两个文件请 `fly logs` 或 Render 的 Logs 面板**（平台通常只保留最近若干行） |
| Render 经常休眠 | 免费版特性。加个定时探活 `/mode`，或改用 Fly |
| 大陆用户连不上/极慢 | 海外平台直连不稳。改用国内 VPS 自建，或 SakuraFrp 内网穿透（见根目录 README） |

---

## 9. 费用/额度小结（截至 2026）

- **Render 免费**：750 实例小时/月（跑 1 台常驻约 744h，刚好够）、512MB、0.1 CPU、5GB 带宽、空闲 15 分钟休眠。
- **Fly.io**：背靠「免费配额」（约 1 台 `shared-cpu-1x / 256MB` 常驻 + 3GB 存储 + 160GB 流量）在免费范围内，超出按量计（256MB 常驻约 $2/月）。**需要绑卡**。

如果你是**大陆网络**且这两家连不稳，最省心的免费替代其实是把中继换成 **SakuraFrp 内网穿透**（手机零安装、国内节点好连），或用一台低配国内 VPS 直接 `pip install -r requirements-relay.txt && python relay.py`（VPS 上同样记得 `--xf-trusted` + HTTPS 用 Caddy）。