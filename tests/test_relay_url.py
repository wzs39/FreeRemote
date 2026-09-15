# -*- coding: utf-8 -*-
"""中继地址归一化锁：run.bat / 手机端输入的各种形态都要收敛到同一形式。"""
import pytest

from free_remote.relay import _normalize_relay_url


@pytest.mark.parametrize("raw,expect", [
    ("https://app.example.link/?id=TEST01", "wss://app.example.link"),
    ("https://app.example.link", "wss://app.example.link"),
    ("http://192.168.1.5:9090", "ws://192.168.1.5:9090"),
    ("ws://r.example.com:9000", "ws://r.example.com:9000"),
    ("wss://r.example.com", "wss://r.example.com"),
    ("  r.example.com  ", "wss://r.example.com"),
    ("https://app.x.link/?id=A&pass=B", "wss://app.x.link"),
])
def test_normalize(raw, expect):
    assert _normalize_relay_url(raw) == expect


def test_query_and_fragment_stripped():
    out = _normalize_relay_url("https://app.x.link/?id=TEST01&pass=x#frag")
    assert "?" not in out and "#" not in out
