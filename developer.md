# FreeRemote 开发者文档

手机浏览器远程控制 Windows 电脑：实时画面（分块 JPEG 增量推流）+ 输入注入（SendInput）+ 文件互传。纯 Python 标准库 + aiohttp + Pillow + numpy，无前端构建链。

## 顶层结构

```
server.py            12 行兼容薄壳 → free_remote.__main__:main（run.bat / 文档入口不变）
relay.py             12 行兼容薄壳 → free_remote.relay（独立中继服务器入口）
hot.py               热更新看门狗：监听 WATCH_FILES 的 mtime，变更即杀掉服务由自身重启
start.bat / stop.bat 一键启动（后台+健康检查）/ 一键停止（先杀看门狗防复活）
web/index.html       CSS + HTML 标记 + 一行 <script type="module">（无业务逻辑）
web/js/*.js          前端 15 个 ES 模块
free_remote/         后端包（单一职责模块，见下）
tests/               pytest 行为锁（116 项）
```

## 后端模块与依赖方向

依赖严格单向，禁止反向（历史上 capture→command 的反向依赖造成过状态环，已清偿）：

```
__main__ ─┬─► web ──► capture / command / fileshare / webcore
          └─► relay(中继客户端)
capture ─► injection ─► win_input / pyautogui
command ─► injection（注入门面 + 修饰/全键释放）
webcore / tokens / netinfo / logging_util / config / health 为横切层，谁都可依赖，它们不依赖业务模块
```

| 模块 | 职责 | 关键所有权 |
|---|---|---|
| `capture.py` | 屏幕采集、JPEG 编码、分块 diff、推流广播 | `Streamer`（帧尺寸/画质/观看者注册）、`FrameSource`（共享采集）、`BlockEncoder`（**协议字节格式，前端兼容生命线**）、`Broadcaster`（MJPEG 备胎） |
| `injection.py` | 注入引擎选择（win_input / pyautogui 回退）的唯一所有者 | `INJ` 门面、`force_release_all_keys/mods` |
| `win_input.py` | ctypes SendInput 直通引擎 | `_held`（**全部按住键的唯一事实来源**）、鼠标/键盘标志常量（有测试锁） |
| `command.py` | 指令调度：`apply_command(streamer, cmd)` | 坐标/参数校验（`_frame_xy`：必须数字、拒 NaN/inf）、count 钳制、按钮白名单、修饰键事务性回滚 |
| `web.py` | aiohttp 路由（`/`、`/info`、`/vstream`、`/ws`、`/files`…`/js/{name}`） | `make_app`；断线 finally 全量释放按键 |
| `webcore.py` | 鉴权（`auth_ok` 顺序：限流→IP→会话→token）、会话 TTL、审计 | `RateLimiter`、`new_session/session_ok` |
| `tokens.py` | 口令生成/加载（token.txt 带 7 天过期时间戳） | `gen_token`、`load_token_file` |
| `relay.py` | 中继服务器（手机↔电脑消息路由、心跳、会话日志）+ 电脑端客户端 | 手机 ctl 断线时**合成 releasekeys 下发电脑**（PC 对手机断线不可见，连接由心跳维持） |
| `fileshare.py` | 文件互传 | `resolve_fs_path`：相对路径一律以共享目录为根（防 CWD 漂移与穿越） |
| `health.py` | 自检（采集/编码/注入/服务存活） | 经 `Streamer.viewer_count()` 公共 API 查询，不窥探私有属性 |
| `__main__.py` | 启动装配、横幅（实时地址/二维码）、端口占用兜底（退出码 3） | |

## 前端模块（web/js/，ES modules）

`state.js` 是唯一事实来源，其余模块全部单向依赖它，模块间**零环**（两处经依赖反转例外：`ctl.bindSend`、prefs→view/video/bigmode 单向）：

| 模块 | 职责 |
|---|---|
| `state.js` | 页面参数/连接状态/推流状态/偏好/修饰键 UI 态；DOM 引用（`initDom` 在模块体内自初始化，因 ES 依赖图它最先执行）；URL 工具 |
| `ctl.js` | 控制 WS：连接、断线 1.5s 永久重连、重连后重发缓存全帧请求 |
| `video.js` | 分块 canvas 渲染 + 冻结检测（超时自动降级） |
| `mjpeg.js` | MJPEG 降级保命路径 |
| `gestures.js` | 触摸手势状态机：点击/拖动/双指捏合缩放/双击复位 |
| `control.js` / `bigmode.js` | 工具栏动作 / 大屏悬浮条拖拽 |
| `files.js` / `prefs.js` / `keyboard.js` | 文件面板 / 操作偏好持久化 / 虚拟键盘 |
| `info.js` / `quality.js` / `view.js` / `login.js` / `main.js` | 状态轮询 / 画质三档 / 画面尺寸 / 口令登录 / 启动装配 |

## 两个运行模式

- **直连（局域网/Tailscale）**：手机 → PC 的 8080（`web.py` 全部路由）。同一进程完成采集+注入。
- **中继**：PC 以 `--relay wss://…` 连中继 `relay.py`；手机浏览器打开中继地址，HTTP 由中继代理、控制消息经 `/ctl` WS 路由。协议消息格式与直连完全一致。

## 状态所有权原则（改代码前先看这里）

- 每份可变状态只有一个所有者模块，其他模块经公共 API 读写（例：按住键→`win_input._held`；观看者→`Streamer.viewer_count()`；前端共享态→`state.js`）。**禁止** `getattr(obj, "_private")` 窥探——历史上有过，已被测试锁死。
- 键鼠安全不变量：任何注入都必须可回滚（`_with_mods` 失败时回滚已按下的修饰键）；断线必须全量释放（web finally / 中结合成指令两条路径都有测试）。
- 协议字节（`BlockEncoder`）与鼠标标志常量是兼容生命线，改动必须先改测试。

## 常用命令

```bash
.venv/Scripts/python.exe -m pytest tests/ -q        # 全部行为锁
.venv/Scripts/python.exe -m pyflakes free_remote/ server.py tests/ hot.py   # CI 同款门禁（零告警）
python server.py                                    # 前台启动；用户日常用 start.bat
python doctor.py                                    # 自检
```

CI（GitHub Actions，windows-latest）：pyflakes 零告警 + 全部测试，push/PR 触发。注意 tests/ 里不得有本机绝对路径（CI checkout 路径不同，曾因此红灯）。

## 热更新

`hot.py` 轮询 `WATCH_FILES`（全部前端 js + 后端核心模块）mtime，变更即重启服务；前端页面本身断线自动重连。新增模块文件时**必须**同步加进 `hot.py` 的监听表，否则改动不生效。
