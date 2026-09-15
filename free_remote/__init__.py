# -*- coding: utf-8 -*-
"""FreeRemote —— 手机浏览器远程控制电脑（监看 + 控制）。

模块结构：
  config        常量与路径
  logging_util  统一日志
  tokens        口令生成/持久化
  health        健康监控（自愈策略）
  netinfo       网络信息/二维码
  capture       屏幕采集 + 分块增量编码
  win_input     Windows SendInput 注入引擎
  command       控制指令调度
  webcore       鉴权/会话/审计原语
  fileshare     文件互传
  relay         识别码中继模式
  web           路由与装配
  __main__      命令行入口
"""

__version__ = "1.0.0"
