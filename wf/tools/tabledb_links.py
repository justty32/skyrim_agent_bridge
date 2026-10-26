#!/usr/bin/env python3
"""tabledb_links — 資料檔（wf-table/1）裡連結欄位的抽取與解析。

CLI 入口在 tabledb.py（links / check / open / resolve），指令實作在本檔 cli()。

規則（見 workflows/common/data-files.md）
  * 連結一律**相對資料檔所在目錄**解析；`#anchor` 解析時去掉。
  * `http(s):`／`mailto:`／`#` 開頭不算連結。
  * 所有欄位都掃 `[label](target)`；**連結欄**（meta `link_columns` ∪ 欄名結尾
    `_path` / `_link`）另外把裸值整個當路徑（該值本身沒有 `](` 時）。
  * 巢狀物件／陣列裡的字串也掃 md 連結，`column` 一律報最上層欄名。
  * `$fmt` 指示詞（見 data-files-fmt.md）先展開再抽連結，該項多一個 `raw`（原模板）；
    展開後是絕對路徑就把 `target` 改寫成相對本 json 目錄，`resolved` 仍是絕對路徑。
    展開失敗／壞指示詞的項：`target`／`resolved` 為 null、`exists` false、加 `error`。
"""
import os
import re

import tabledb_fmt

LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
EXT = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.\-]*:|#|<)")


def link_columns(table):
    """該表哪些欄位的裸值要當路徑。"""
    cols = set(table.meta.get("link_columns") or [])
    cols.update(c for c in table.columns
                if c.endswith("_path") or c.endswith("_link"))
    return cols


def _in_string(s, bare):
    """一個字串裡的連結目標（含 #anchor），照出現順序。"""
    found = LINK.findall(s)
    if bare and not found:
        found = [s.strip()]
    return [t for t in found if t and not EXT.match(t)]


def _entry(i, col, t, base, raw):
    path = t.split("#", 1)[0]
    if not path:
        return None
    res = os.path.normpath(os.path.join(base, path))
    if path.startswith("/"):  # 展開出來的絕對路徑 → target 改成相對 json 目錄
        frag = t.split("#", 1)[1:]
        t = os.path.relpath(res, base) + ("#" + frag[0] if frag else "")
    e = {"index": i, "column": col, "target": t,
         "resolved": res, "exists": os.path.exists(res)}
    if raw is not None:
        e["raw"] = raw
    return e


def entries(table, base=None):
    """每筆每欄的連結：[{index, column, target, resolved, exists}]（+ raw / error）。"""
    base = base or os.path.dirname(os.path.abspath(table.path))
    lcols = link_columns(table)
    out = []
    for i, row in enumerate(table.rows):
        cols = list(table.columns)
        cols += [c for c in row if c not in cols]
        for col in cols:
            if col not in row:
                continue
            for p in tabledb_fmt.parts(row[col], base):
                if p["error"]:
                    out.append({"index": i, "column": col, "target": None,
                                "resolved": None, "exists": False,
                                "raw": p["raw"], "error": p["error"]})
                    continue
                for t in _in_string(p["s"], col in lcols and p["top"]):
                    e = _entry(i, col, t, base, p["raw"])
                    if e:
                        out.append(e)
    return out


def broken(table, base=None):
    return [e for e in entries(table, base) if not e["exists"]]


def pick(table, i, col=None):
    """第 i 筆的第一個連結（給了 COL 就限定該欄）；沒有就 None。跳過展開失敗的項。"""
    es = [e for e in entries(table) if e["index"] == i and not e.get("error")]
    if col:
        es = [e for e in es if e["column"] == col]
    return es[0] if es else None


def cli(cmd, table, rest, emit):
    """tabledb.py 的 links / check / open / resolve：印結果、回結束碼。"""
    if cmd == "links":
        emit(entries(table))
        return 0
    if cmd == "check":
        bad = broken(table)
        emit(bad)
        return 1 if bad else 0
    col = rest[1] if len(rest) > 1 else None
    e = pick(table, int(rest[0]), col)
    if e is None:
        emit({"error": f"no link in row {rest[0]}" + (f", column {col!r}" if col else "")})
        return 1
    if cmd == "resolve":
        emit(e)
        return 0
    try:
        with open(e["resolved"], encoding="utf-8") as f:
            emit({"path": e["resolved"], "content": f.read()})
    except OSError as exc:
        emit({"error": str(exc)})
        return 1
    return 0
