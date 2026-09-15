# -*- coding: utf-8 -*-
"""webcore 锁：鉴权顺序、会话 TTL、限流、IP 白名单。"""
import time

from free_remote import webcore
from free_remote.webcore import (
    RateLimiter, ip_allowed, new_session, session_ok,
)


class FakeReq:
    def __init__(self, headers=None, cookies=None, args=None, app=None, remote="127.0.0.1"):
        self.headers = headers or {}
        self.cookies = cookies or {}
        self._args = args or {}
        self.query = self._args
        self.app = app if app is not None else {"args": type("A", (), {
            "allow_ips": set(), "no_auth": False, "token": "PW"})()}
        self.remote = remote
        self.resp_cookies = None

    def __getitem__(self, k):
        return self._args[k]

    def get(self, k, d=None):
        return self._args.get(k, d)

    @property
    def transport(self):
        class T:
            def get_extra_info(self, _):
                return (self.remote, 0)
        return T()


def _auth_app(monkeypatch, token="PW", allow=set()):
    app = {"args": type("A", (), {"allow_ips": allow, "no_auth": False, "token": token})(),
           "sessions": {}}
    return app


def test_auth_ok_with_url_token(monkeypatch):
    app = _auth_app(monkeypatch)
    assert webcore.auth_ok(FakeReq(args={"token": "PW"}, app=app)) is True


def test_auth_ok_rejects_wrong_token(monkeypatch):
    app = _auth_app(monkeypatch)
    assert webcore.auth_ok(FakeReq(args={"token": "WRONG"}, app=app)) is False


def test_session_flow(monkeypatch):
    app = _auth_app(monkeypatch)
    sid = new_session(app, "PW")
    assert session_ok(app, sid) is True
    assert session_ok(app, "bogus") is False


def test_session_expires(monkeypatch):
    app = _auth_app(monkeypatch)
    sid = new_session(app, "PW")
    app["sessions"][sid]["exp"] = time.time() - 1  # 人为过期
    assert session_ok(app, sid) is False


def test_ip_allowlist_blocks_strangers(monkeypatch):
    # 行为锁：白名单为空 = 不限制（历史行为，部署文档依赖此默认）
    assert ip_allowed(FakeReq(remote="10.0.0.5")) is True
    req_maker = lambda r: FakeReq(args={}, app=_auth_app(monkeypatch, allow={"192.168.1.10"}), remote=r)
    assert ip_allowed(req_maker("192.168.1.10")) is True
    assert ip_allowed(req_maker("10.0.0.5")) is False


def test_no_auth_bypasses(monkeypatch):
    app = {"args": type("A", (), {"allow_ips": set(), "no_auth": True, "token": "x"})(),
           "sessions": {}}
    assert webcore.auth_ok(FakeReq(args={"token": "nope"}, app=app)) is True


def test_ratelimiter_lockout():
    rl = RateLimiter(max_fail=3, window=60, lockout=60)
    for _ in range(3):
        assert rl.allow("1.2.3.4") is True  # 前三次放行并计数
        rl.note_fail("1.2.3.4")
    assert rl.allow("1.2.3.4") is False  # 第四次锁定
    assert rl.allow("5.6.7.8") is True    # 其他 IP 不受影响
