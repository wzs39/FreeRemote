# -*- coding: utf-8 -*-
"""BlockEncoder 协议锁：消息格式与增量语义是手机端兼容的生命线。

协议（二进制）：
  0x01 全帧: [type][2B w][2B h][4B jpegLen][jpeg][2B cx][2B cy]
  0x02 分块: [type][2B count][每块: 2B 块号][2B len][jpeg]...[2B cx][2B cy]
  0x04 光标: [type][2B x][2B y]
"""
import struct

from free_remote.capture import BlockEncoder

W, H = 256, 128  # 4x2 块


def make_frame(paint_blocks=(), rgb=(200, 90, 10), size=(W, H)):
    from PIL import Image
    im = Image.new("RGB", size, (30, 30, 40))
    px = im.load()
    for (row, col) in paint_blocks:
        for x in range(col * 64, min(col * 64 + 64, size[0])):
            for y in range(row * 64, min(row * 64 + 64, size[1])):
                px[x, y] = rgb
    return im.tobytes()


def test_first_frame_is_full_frame(encoder):
    msgs = encoder.diff(make_frame(), W, H, 5, 5)
    assert len(msgs) == 1 and msgs[0][0] == 0x01
    w, h, jl = struct.unpack(">HHI", msgs[0][1:9])
    assert (w, h) == (W, H)
    assert jl == len(msgs[0]) - 13  # 头 9B + jpeg + 光标 4B
    assert msgs[0][9:9 + jl].startswith(b"\xff\xd8")  # JPEG SOI


def test_static_frame_is_zero_traffic(encoder):
    f = make_frame()
    encoder.diff(f, W, H, 5, 5)
    assert encoder.diff(f, W, H, 5, 5) == []


def test_cursor_only_move(encoder):
    f = make_frame()
    encoder.diff(f, W, H, 5, 5)
    msgs = encoder.diff(f, W, H, 9, 7)
    assert len(msgs) == 1 and msgs[0][0] == 0x04
    assert struct.unpack(">HH", msgs[0][1:5]) == (9, 7)


def test_single_block_message(encoder):
    encoder.diff(make_frame(), W, H, 5, 5)
    msgs = encoder.diff(make_frame(paint_blocks=[(1, 2)]), W, H, 5, 5)
    assert len(msgs) == 1 and msgs[0][0] == 0x02
    count = struct.unpack(">H", msgs[0][1:3])[0]
    assert count == 1
    idx, blen = struct.unpack(">HH", msgs[0][3:7])
    assert idx == 1 * 4 + 2  # row-major
    jpeg = msgs[0][7:7 + blen]
    assert jpeg.startswith(b"\xff\xd8")
    cx, cy = struct.unpack(">HH", msgs[0][7 + blen:11 + blen])
    assert (cx, cy) == (5, 5)


def test_multi_block_count_matches_payload(encoder):
    blocks = [(0, 0), (1, 1), (1, 3)]
    encoder.diff(make_frame(), W, H, 5, 5)
    msgs = encoder.diff(make_frame(paint_blocks=blocks), W, H, 5, 5)
    m = msgs[0]
    assert m[0] == 0x02
    count = struct.unpack(">H", m[1:3])[0]
    # 遍历 payload 校验块号与长度自洽
    off, seen = 3, []
    for _ in range(count):
        idx, blen = struct.unpack(">HH", m[off:off + 4])
        seen.append(idx)
        off += 4 + blen
    assert off + 4 == len(m)  # 尾部光标
    assert sorted(seen) == sorted(r * 4 + c for r, c in blocks)


def test_large_change_falls_back_to_full_frame(encoder):
    blocks = [(r, c) for r in range(2) for c in range(4)]  # 100% 变化
    encoder.diff(make_frame(), W, H, 5, 5)
    msgs = encoder.diff(make_frame(paint_blocks=blocks), W, H, 5, 5)
    assert len(msgs) == 1 and msgs[0][0] == 0x01


def test_non_aligned_edge_block(encoder):
    """边缘残块（尺寸不足 64）也要正确切片与编码。"""
    w2, h2 = 200, 100
    enc = BlockEncoder(type("S", (), {"quality": 70})())
    enc.diff(make_frame(size=(w2, h2)), w2, h2, 1, 1)
    msgs = enc.diff(make_frame(paint_blocks=[(1, 3)], size=(w2, h2)), w2, h2, 1, 1)
    assert msgs[0][0] == 0x02
    idx = struct.unpack(">H", msgs[0][3:5])[0]
    assert idx == 1 * 4 + 3


def test_snapshot_tracks_frame_size_change(encoder):
    f1 = make_frame(size=(W, H))
    encoder.diff(f1, W, H, 5, 5)
    f2 = make_frame(size=(128, 64))
    msgs = encoder.diff(f2, 128, 64, 5, 5)
    assert msgs[0][0] == 0x01  # 尺寸变化 → 全帧重发
