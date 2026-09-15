# -*- coding: utf-8 -*-
"""口令生命周期锁：真实生成格式（单词-数字）、读写、时效、向后兼容。"""
import time

from free_remote import tokens
from free_remote.tokens import gen_token, load_token_file, save_token_file

TOKEN_WORDS_SET = set(tokens.TOKEN_WORDS)


def test_gen_token_format():
    """真实格式：maple-otter-luna-724（3 词 + 2 位数）。"""
    for _ in range(50):
        t = gen_token()
        parts = t.split("-")
        assert len(parts) == 4, t
        assert all(w in TOKEN_WORDS_SET for w in parts[:3]), t
        assert parts[3].isdigit() and len(parts[3]) == 2, t


def test_gen_token_uniqueness():
    assert len({gen_token() for _ in range(100)}) >= 99  # 词表有限，允许极小概率碰撞


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "BASE_DIR", tmp_path)
    # 契约：expire 必须为 int（生产端 int(time.time())+7d；float 会被 int() 解析拒绝）
    exp = int(time.time()) + 3600
    save_token_file("maple-otter-luna-724", exp)
    tok, exp2 = load_token_file()
    assert tok == "maple-otter-luna-724"
    assert exp2 == exp


def test_save_no_rewrite_when_unchanged(tmp_path, monkeypatch):
    """内容不变不写盘（避免触发看门狗热更新）——这是 load 的「过期续期不换口令」策略的基础。"""
    monkeypatch.setattr(tokens, "BASE_DIR", tmp_path)
    cf = tmp_path / "token.txt"
    save_token_file("same-token", 12345)
    mtime1 = cf.stat().st_mtime_ns
    time.sleep(0.01)
    save_token_file("same-token", 12345)
    assert cf.stat().st_mtime_ns == mtime1


def test_expired_token_extends_one_year_same_password(tmp_path, monkeypatch):
    """过期 → 同口令延长 1 年（手机书签永不失效），不换新口令。"""
    monkeypatch.setattr(tokens, "BASE_DIR", tmp_path)
    save_token_file("maple-otter-luna-724", int(time.time()) - 10)
    tok, exp = load_token_file()
    assert tok == "maple-otter-luna-724"  # 口令不变
    assert exp > time.time() + 300 * 86400  # 延长约 1 年
    """旧版单行文件（无时间戳）→ 永久有效（exp=None）。"""
    monkeypatch.setattr(tokens, "BASE_DIR", tmp_path)
    (tmp_path / "token.txt").write_text("LegacyTok\n", encoding="utf-8")
    tok, exp = load_token_file()
    assert tok == "LegacyTok"
    assert exp is None


def test_missing_file_returns_none_none(tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "BASE_DIR", tmp_path)
    assert load_token_file() == (None, None)
