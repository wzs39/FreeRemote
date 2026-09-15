# -*- coding: utf-8 -*-
"""pytest 共享夹具。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class FakeStreamer:
    """BlockEncoder 只依赖 quality 与帧尺寸换算，够用即可。"""

    quality = 70
    scale = 0.5
    monitor = {"left": 0, "top": 0}

    def frame_to_screen(self, x, y):
        return x / self.scale, y / self.scale


@pytest.fixture
def fake_streamer():
    return FakeStreamer()


@pytest.fixture
def encoder(fake_streamer):
    from free_remote.capture import BlockEncoder
    return BlockEncoder(fake_streamer)
