# FreeRemote + Tailscale：手机跨网络直连电脑（推荐方案）

> 用 Tailscale 把「电脑 ＋ 手机」组进同一个虚拟局域网，**不经过任何登录门禁、不需要中继服务器、不需要公网 IP**。
> 两台设备无论在哪个网络（家里 WiFi / 公司 WiFi / 4G / 5G）都能直连，端到端加密（WireGuard）。

## 架构

```
手机 ──┬── Tailscale 虚拟局域网 ──┬── 电脑（FreeRemote server.py :8080）
       └──────────────────────────┘          （手机直连电脑，无中转）
```

- 电脑运行 FreeRemote（局域网模式即可），手机浏览器打开电脑的 Tailscale 地址
- WebSocket 原生放行，延迟最低（两端直连，不走公网中转）

---

## 第一步：注册 Tailscale 账号（一次，约 2 分钟）

1. 打开 <https://tailscale.com> → **Get started / Sign up**（可用 Google / GitHub / 微软账号登录）
2. 记下登录用的账号（后面电脑和手机都用**同一个账号**登录）

## 第二步：电脑装 Tailscale（Windows，约 3 分钟）

1. 下载安装：<https://tailscale.com/download/windows>
2. 打开 Tailscale 客户端 → 用**同一个账号**登录 → 状态变为 **Connected**
3. 查看本机的 Tailscale 地址（100.x.y.z）：
   ```
   tailscale ip -4
   ```
   或者：任务栏 Tailscale 图标 → 点开就能看到本机 IP，形如 `100.73.214.88`

4. 启动 FreeRemote（局域网模式即可，Tailscale 地址已在监听范围内）：
   - 双击 `run.bat`，或
   - `python server.py --port 8080 --token 你的口令`
   - 启动横幅会打印「手机访问(Tailscale跨网) : http://100.x.y.z:8080/?token=你的口令」

> **固定口令 + 有效期（推荐）**：双击 `renew-token.bat` 一键生成新口令（默认 7 天有效），
> 横幅显示有效期与剩余天数；口令过期后再启动会**自动续期 7 天换新**（横幅提示新链接），
> 运行中到期会立即拒绝新连接。手机书签里的口令要跟着更新。

> 想更安全可把服务只绑到 Tailscale 地址：`python server.py --host 100.x.y.z --port 8080`
> （这样局域网里其他设备也访问不到）。

## 第三步：手机装 Tailscale（约 3 分钟）

1. 应用商店（App Store / 各安卓应用商店）搜索 **Tailscale** 安装（官方应用，认准图标）
2. 打开 App → 用**同一个账号**登录 → 开启 VPN（显示 **Connected**）
3. **手机必须保持 VPN 开启**才能访问电脑的 100.x 地址

## 第四步：手机使用

1. 手机浏览器打开（可存书签）：
   ```
   http://100.x.y.z:8080/?token=你的口令
   ```
2. 进入远程桌面，即可监看和控制电脑；文件面板可互传文件

---

## 安全建议

- 电脑别关机/别休眠，否则手机连不上（远程控制本来就要求电脑开着）
- FreeRemote 使用固定口令（`--token`），不要泄露带 `?token=` 的完整链接
- Tailscale 管理台（<https://login.tailscale.com/admin/machines>）能看到设备清单，不用的设备可移除

## 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| 手机能开 App 但打不开网页 | 手机 VPN 是否开启；确认访问的是 `http://100.x.y.z:8080/`（电脑的 Tailscale IP + FreeRemote 端口） |
| 手机和电脑不在同一账号 | Tailscale 两端必须登录**同一个**账号才能互访 |
| 打开提示「需要访问口令」 | 网址要带 `?token=你的口令`，或在页面输入框里填口令 |
| 电脑的 Tailscale IP 变了 | Tailscale 地址一般固定；变了的话重新在横幅里看「手机访问(Tailscale跨网)」一行 |
| 想用域名代替 IP | Tailscale 管理台给电脑起个名字（如 `my-pc`），之后用 `http://my-pc:8080/` |