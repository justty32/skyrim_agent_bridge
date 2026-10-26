#!/usr/bin/env python3
"""fix_moved_links_scan — 要掃哪些檔，以及「舊路徑 → 新路徑」的搬移表。

CLI 入口在 fix_moved_links.py，這裡只放掃描與查表那半段：

  git_root(dir)             ROOT 的預設值（`git rev-parse`，不在 git 裡就用 dir 本身）
  files(root)               要重寫的檔：`*.md`、`*.csv`、含 `"contract": "wf-table/` 的 `*.json`
  load_moves(specs, root)   讀 moves.tsv 填 FILES／DIRS，並把 a→b→c 收斂成 a→c
  remap(abs_target)         目標被搬到哪；沒搬就回 None
  old_dir_of(f)             f 的現路徑 → 它被搬之前所在的目錄

FILES／DIRS 是模組層狀態，`load_moves()` 之後才有值。
"""
import os
import subprocess

FILES, DIRS = {}, {}


def git_root(d):
    try:
        return subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=d,
                              capture_output=True, check=True).stdout.decode().strip()
    except (OSError, subprocess.CalledProcessError):
        return os.path.abspath(d)


def _is_table(p):
    try:
        return '"contract": "wf-table/' in open(p, encoding="utf-8").read(8192)
    except (UnicodeDecodeError, OSError):
        return False


def files(root):
    try:
        out = subprocess.run(["git", "ls-files", "--recurse-submodules", "-z"],
                             cwd=root, capture_output=True, check=True).stdout.decode()
        rels = [p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        rels = []
        for r, ds, fs in os.walk(root):
            ds[:] = [d for d in ds if d not in (".git", "node_modules")]
            rels += [os.path.relpath(os.path.join(r, f), root) for f in fs]
    keep = []
    for rel in rels:
        ext = os.path.splitext(rel)[1]
        if ext not in (".md", ".json", ".csv"):
            continue
        a = os.path.join(root, rel)
        if not os.path.exists(a) or (ext == ".json" and not _is_table(a)):
            continue
        keep.append(a)
    return keep


def load_moves(specs, root):
    for p, prefix in specs:
        base = os.path.join(root, prefix) if prefix else root
        for line in open(p, encoding="utf-8"):
            line = line.rstrip("\n")
            if not line or line.startswith("#") or "\t" not in line:
                continue
            old, new = line.split("\t")[:2]
            old = os.path.normpath(os.path.join(base, old.strip()))
            new = os.path.normpath(os.path.join(base, new.strip()))
            (DIRS if os.path.isdir(new) else FILES)[old] = new
    # a->b 接 b->c 收斂成 a->c
    for table in (FILES, DIRS):
        for old in list(table):
            seen, cur = {old}, table[old]
            while cur in table and cur not in seen:
                seen.add(cur); cur = table[cur]
            table[old] = cur


def remap(abs_target):
    if abs_target in FILES:
        return FILES[abs_target]
    for old_dir, new_dir in DIRS.items():
        if abs_target == old_dir or abs_target.startswith(old_dir + os.sep):
            return new_dir + abs_target[len(old_dir):]
    return None


def old_dir_of(f):  # f 的現路徑 → 它被搬之前所在的目錄
    for old, new in FILES.items():
        if new == f:
            return os.path.dirname(old)
    for old, new in DIRS.items():
        if f == new or f.startswith(new + os.sep):
            return os.path.dirname(old + f[len(new):])
    return os.path.dirname(f)
