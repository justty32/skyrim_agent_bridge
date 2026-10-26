#!/usr/bin/env python3
"""tabledb_fmt_expand — `$fmt` 的變數解析：目錄往上找出算法的值，代換 `${…}`。

門面與說明在 tabledb_fmt.py（外部一律 `import tabledb_fmt`，不必直接 import 本檔）；
名字→算法的對映在 tabledb_fmt_vars.py，本檔只實作算法與代換。
"""
import os
import re
import sys

import tabledb_fmt_vars as fv

VAR = re.compile(r"\$\{([^{}]*)\}")
BAD = "bad $fmt directive: need exactly one key $fmt with a string value"
FmtError = fv.FmtError
_CTX = {}


def is_directive(v):
    return isinstance(v, dict) and len(v) == 1 and isinstance(v.get("$fmt"), str)


def context(json_dir):
    """json 目錄的變數表；目錄可以不存在（純目錄往上找，不呼叫 git）。"""
    d = os.path.abspath(json_dir)
    if d not in _CTX:
        roots, cur = [], d
        while True:
            if os.path.exists(os.path.join(cur, ".git")):
                roots.append(cur)
            up = os.path.dirname(cur)
            if up == cur:
                break
            cur = up
        if not roots:  # cache 保證每個目錄只印一次
            print("tabledb: no .git at or above %s; git-self/git-parent/git-top"
                  " fall back to file-dir" % d, file=sys.stderr)
            roots = [d]
        _CTX[d] = {"file-dir": d, "git-self": roots[0],
                   "git-parent": roots[1] if len(roots) > 1 else roots[0],
                   "git-top": roots[-1]}
    return _CTX[d]


def spec(json_dir):
    """該 json 適用的合併後變數表（見 tabledb_fmt_vars 的載入順序）。"""
    c = context(json_dir)
    return fv.load(c["file-dir"], c["git-self"])


def names(json_dir):
    c = context(json_dir)
    return fv.names(c["file-dir"], c["git-self"])


def canon(json_dir):
    c = context(json_dir)
    return fv.canon(c["file-dir"], c["git-self"])


def expand(template, ctx):
    vmap = fv.names(ctx["file-dir"], ctx["git-self"])

    def one(m):
        name = m.group(1)
        how = vmap.get(name)
        if how in ctx:
            return ctx[how]
        pre, sep, arg = name.partition(":")
        if sep and vmap.get(pre) == "env":
            v = os.environ.get(arg)
            if v is None:
                raise FmtError("unknown environment variable %r in ${%s}" % (arg, name))
            return v
        raise FmtError("unknown variable ${%s}; see: tabledb.py fmt --vars" % name)
    return VAR.sub(one, template)
