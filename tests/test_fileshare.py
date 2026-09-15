# -*- coding: utf-8 -*-
"""fileshare 路径解析锁：相对路径以共享目录为根；共享根外访问被拒。"""
import os

from free_remote.fileshare import resolve_fs_path, safe_file_name


def test_relative_path_rooted_at_share_dir(tmp_path):
    root = str(tmp_path)
    out = resolve_fs_path(root, "sub/dir/file.txt")
    assert out == os.path.abspath(os.path.join(root, "sub", "dir", "file.txt"))


def test_empty_path_returns_root(tmp_path):
    assert resolve_fs_path(str(tmp_path), "") == str(tmp_path)


def test_absolute_path_kept(tmp_path):
    p = os.path.abspath(os.path.join(str(tmp_path), "x.bin"))
    assert os.path.abspath(resolve_fs_path(str(tmp_path), p)) == p


def test_safe_file_name_blocks_traversal():
    assert safe_file_name("..\evil") not in ("..\evil",)
    assert "/" not in safe_file_name("a/b") and "\\" not in safe_file_name("a/b")
    assert safe_file_name("ok-name.txt") == "ok-name.txt"
