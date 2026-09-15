# -*- coding: utf-8 -*-
"""全局常量与路径。"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # 包目录的上一级 = 项目根（token.txt/logs/web 所在地）

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "server.log"


QUALITY_PRESETS = {
    "low": (0.35, 45),    # 流畅：适合弱网
    "mid": (0.6, 65),     # 均衡（默认）
    "high": (1.0, 88),    # 高清：局域网
}


BOUNDARY = b"frame"

