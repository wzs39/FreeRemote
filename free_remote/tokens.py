# -*- coding: utf-8 -*-
"""口令生成与持久化（token.txt 单一所有者）。"""
import secrets
import time

from .config import BASE_DIR

TOKEN_WORDS = (
    "maple otter luna nova pixel mango tiny echo sunny comet lucky "
    "fox river cloud happy panda kiwi amber noble cactus cedar cherry "
    "cobra coral daisy delta drift ember falcon flint grove hazel "
    "iris jade lemon lotus meadow neon olive pearl raven solar tulip "
    "vivid wren zebra anchor brush camel dune fable garnet herb jolly "
    "koala mint nutmeg opal quail reef sage thyme"
).split()


def gen_token() -> str:
    """直观口令：3 个小写单词 + 2 位数字，如 maple-otter-luna-724。

    组合约 75^3 * 90 ≈ 2^30，配合登录限速足够安全，且比随机字符串
    好念、好记、好手输（口头转述也不容易错）。
    """
    words = "-".join(secrets.choice(TOKEN_WORDS) for _ in range(3))
    return f"{words}-{secrets.randbelow(90) + 10}"


def save_token_file(token: str, expire) -> None:
    """保存口令到 token.txt；expire=None 表示永久。内容无变化时不写盘（避免触发热更新）。"""
    cf = BASE_DIR / "token.txt"
    content = token if not expire else "{}\n{}".format(token, expire)
    try:
        if cf.exists() and cf.read_text(encoding="utf-8").strip() == content.strip():
            return
    except OSError:
        pass
    cf.write_text(content, encoding="utf-8")


def load_token_file():
    """读 token.txt → (口令, 过期时间戳或 None=永久)。

    兼容历史格式（纯口令 / 口令+时间戳 / 旧随机串）。已过期时不换口令，
    只把有效期延长 1 年——口令不变，手机书签永远不用更新。
    """
    cf = BASE_DIR / "token.txt"
    if not cf.exists():
        return None, None
    try:
        lines = cf.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return None, None
    if not lines or not lines[0].strip():
        return None, None
    token = lines[0].strip()
    expire = None
    if len(lines) >= 2:
        try:
            expire = int(lines[1].strip())
        except ValueError:
            expire = None
        if expire is not None and time.time() > expire:
            expire = int(time.time()) + 365 * 86400  # 已过期：同口令延长 1 年
    save_token_file(token, expire)  # 归一化存储（永久口令只留一行）
    return token, expire

