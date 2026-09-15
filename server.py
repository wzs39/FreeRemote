#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreeRemote —— 手机浏览器远程控制电脑（监看 + 控制）

在电脑上运行：  python server.py [--port 8080] [--token xxxx]
手机浏览器打开：http://<电脑IP>:8080/?token=xxxx

实现已模块化至 free_remote/ 包；本文件是兼容入口（run.bat / 看门狗 / 文档都指向它）。
"""
from free_remote.__main__ import main

if __name__ == "__main__":
    main()
