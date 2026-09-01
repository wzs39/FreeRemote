# 用 Docker 把中继部署到自己的服务器

> 场景：你有一台自己的服务器（VPS / 云主机），想把 FreeRemote 的**中继**打包成 Docker 镜像部署上去，
> 让手机在任何网络（4G/5G/别的 WiFi）都能通过它连回你家的电脑。
> 电脑端（`server.py`）仍然跑在你实际控制的电脑上，不需要进容器。

两种部署形态：

| 形态 | 文件 | 适合场景 |
|---|---|---|
| **直连版（HTTP）** | `docker-compose.yml` | 内网测试、局域网先跑通 |
| **HTTPS 生产版** | `docker-compose.https.yml` + `Caddyfile` | **公网正式使用（推荐）**，自动 HTTPS/wss |

## 一、打包镜像

项目里已有 `Dockerfile`（只装中继的依赖 `aiohttp` + `Pillow`，不含电脑端的 pyautogui/mss，所以能在无桌面的 Linux 服务器上构建运行）。

在项目目录执行：

```bash
docker build -t freeremote-relay:latest .
```

本仓库已实测构建成功，镜像内中继可正常启动（`/mode`、登录、鉴权均验证通过）。

## 二、配置 `.env`

```bash
cp .env.example .env
# 编辑 .env：
#   DOMAIN=relay.example.com        # 你的域名（HTTPS 版必填，需已解析 A 记录到服务器）
#   RELAY_DEVICES=TEST01:你的强口令   # 识别码:口令，可多个用英文逗号分隔
#   RELAY_STRICT=1                  # 1=只允许登记过的识别码连接（强烈建议）
```

> `.env` 含密钥，已在 `.dockerignore` 中排除，不会进镜像或提交 Git。

## 三、启动

**HTTPS 生产版（推荐，公网用）**：

```bash
docker compose -f docker-compose.https.yml up -d --build
```

- Caddy 自动向 Let's Encrypt 申请证书并续期，HTTP 自动跳 HTTPS
- 手机访问 `https://relay.example.com/` → 页面自动走 **wss**，输入识别码+口令登录
- 电脑端连接：`python server.py --relay wss://relay.example.com --id TEST01 --password 你的强口令`
- 中继只暴露在 compose 内网，**公网端口只有 80/443**，攻击面最小

**直连版（先内网试跑）**：

```bash
docker compose up -d --build      # 映射 9090 -> 容器 8080
# 手机访问 http://<服务器IP>:9090/
```

## 四、架构与安全说明

```
手机浏览器 ──wss──▶ Caddy(443, TLS 终结) ──http──▶ relay 容器(8080, 内网)
                                                        ▲
                                  电脑 server.py ──wss 外连（主动连出，无需端口）
```

- **HTTPS 由 Caddy 终结**，容器内无需证书；手机端自动切 wss、电脑端 `--relay wss://...`
- HTTPS 版开启 `--xf-trusted`：Caddy 会把手机真实 IP 追加进 `X-Forwarded-For`，限速/审计/白名单按**真实 IP** 生效
  （直连版不要开，否则客户端可伪造 XFF 绕过限速）
- 内置防护照常生效：会话登录（口令不进 URL）、防暴力破解（同 IP 失败 5 次锁 5 分钟）、
  `security.log` 审计、`sessions.log` 会话日志、心跳监控、同识别码多端策略（replace/reject）
- 中继代码改动后重新部署：`docker compose -f docker-compose.https.yml up -d --build`

## 五、常用运维命令

```bash
docker compose -f docker-compose.https.yml logs -f relay   # 看中继日志
docker compose -f docker-compose.https.yml logs -f caddy   # 看证书/代理日志
docker compose -f docker-compose.https.yml down            # 停止（保留数据卷）
docker exec -it <relay容器名> sh                            # 进容器
docker exec <relay容器名> tail -20 logs/security.log       # 看审计
```

## 六、验证部署是否成功

```bash
curl https://relay.example.com/mode     # 应返回 {"mode": "relay"}
curl https://relay.example.com/         # 应返回手机端页面（v2.x）
```

手机打开 `https://relay.example.com/` → 输入识别码+口令登录 → 状态栏显示「识别码 TEST01 · 在线」即全链路打通。
