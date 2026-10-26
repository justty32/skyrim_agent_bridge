#!/usr/bin/env python3
"""tabledb — 同質列表資料檔（.json / .csv）的統一 CRUD 與連結解析。

契約 `wf-table/1`（見 workflows/common/data-files.md）：md 裡每列同一組欄位、
超過 1 KB 的條列式區塊抽成資料檔，md 只留摘要（不寫查詢指令、不寫工具路徑）；讀寫都走這支，不整份讀進 context。

檔案格式
  .json  {"contract": "wf-table/1", "source": "<抽出自哪份 md>", "extracted": "YYYY-MM-DD",
          "columns": [...], "link_columns": [...], "rows": [{...}, ...]}
         rows 的值可以是多行字串或巢狀物件；缺的欄位視為空字串。舊檔沒有 contract 也讀得開。
         值也可以是路徑代號 {"$fmt": "${gitRoot}/docs/x.md#錨"}：links/check/open/resolve/fmt
         展開後才解析，get/find/grep/--slice 原樣回傳（見 common/data-files-fmt.md）。
  .csv   第一列是欄位名，其餘每列一筆；值一律字串。只適合扁平、無多行值的表；
         沒地方放 link_columns，改用欄名結尾 _path / _link 表示該欄是連結。

CLI（索引一律 0-based；所有輸出都是 JSON）
  tabledb.py FILE                       契約、列數與欄位
  tabledb.py FILE get I                 第 I 筆
  tabledb.py FILE find k=v [k=v ...]    所有欄位精確相等的筆（含索引）
  tabledb.py FILE grep REGEX            任一欄位符合 REGEX 的筆（含索引，不分大小寫）
  tabledb.py FILE add k=v [k=v ...]     追加一筆（未給的欄位補空字串）
  tabledb.py FILE update I k=v [...]    改第 I 筆的指定欄位
  tabledb.py FILE delete I              刪第 I 筆（之後的索引會前移）
  tabledb.py FILE columns               欄位列表
  tabledb.py FILE --slice A B           第 A 到 B-1 筆
  tabledb.py links FILE                 每筆每欄的連結 [{index, column, target, resolved, exists}]
  tabledb.py check FILE                 同上只印壞的（exists=false）；有壞的結束碼 1
  tabledb.py open FILE I [COL]          {path, content}：該筆連結指向的檔案內容
  tabledb.py resolve FILE I [COL]       同上只印 {index, column, target, resolved, exists}
  tabledb.py fmt FILE                   每個 $fmt 指示詞 [{index, column, raw, expanded}]
  tabledb.py fmt --vars [FILE]          變數表：名字／算法／別名／來源（見 tools/fmt-vars.json）
  （後五個也接受 `tabledb.py FILE links` 這種順序。）

Python
  from tabledb import load
  t = load("x.json"); t.rows; t.columns; t.get(3); t.find(id="31472"); t.add({...}); t.update(3, status="GO"); t.delete(3); t.save()
  Table 本身在 tabledb_table；連結相關在 tabledb_links：entries(t) / broken(t) / pick(t, i, col) / link_columns(t)
  $fmt 相關在 tabledb_fmt：is_directive(v) / context(dir) / expand(tpl, ctx) / expand_value(v, ctx)
"""
import json
import sys

import tabledb_fmt
import tabledb_links
from tabledb_table import Table


def load(path):
    return Table.load(path)


LINK_CMDS = ("links", "check", "open", "resolve", "fmt")


# ---- CLI --------------------------------------------------------------
def _kv(args):
    out = {}
    for a in args:
        if "=" not in a:
            raise SystemExit(f"expected k=v, got {a!r}")
        k, v = a.split("=", 1)
        out[k] = tabledb_fmt.value_of(v)
    return out


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[:2] == ["fmt", "--vars"]:  # FILE 只用來決定看哪份 fmt-vars.json
        return tabledb_fmt.vars_cli(argv[2] if len(argv) > 2 else None, _emit)
    if argv[0] in LINK_CMDS:  # links/check/open/resolve/fmt FILE ... 的順序
        if len(argv) < 2:
            raise SystemExit(f"{argv[0]}: missing FILE")
        argv = [argv[1], argv[0]] + argv[2:]
    path, cmd, rest = argv[0], (argv[1] if len(argv) > 1 else None), argv[2:]
    t = Table.load(path)
    if cmd is None:
        _emit({"file": path, "contract": t.meta.get("contract"), "count": len(t),
               "columns": t.columns,
               **{k: v for k, v in t.meta.items() if k != "contract"}})
    elif cmd == "columns":
        _emit(t.columns)
    elif cmd == "get":
        _emit(t.get(int(rest[0])))
    elif cmd == "--slice":
        a, b = int(rest[0]), int(rest[1])
        _emit([{"index": i, **t.rows[i]} for i in range(a, min(b, len(t)))])
    elif cmd == "find":
        _emit([{"index": i, **r} for i, r in t.find_indexed(**_kv(rest))])
    elif cmd == "grep":
        _emit([{"index": i, **r} for i, r in t.grep(rest[0])])
    elif cmd == "add":
        i = t.add(_kv(rest)); t.save(); _emit({"index": i, **t.rows[i]})
    elif cmd == "update":
        r = t.update(int(rest[0]), **_kv(rest[1:])); t.save(); _emit(r)
    elif cmd == "delete":
        r = t.delete(int(rest[0])); t.save(); _emit(r)
    elif cmd == "fmt":
        return tabledb_fmt.cli(t, _emit, rest)
    elif cmd in LINK_CMDS:
        return tabledb_links.cli(cmd, t, rest, _emit)
    else:
        raise SystemExit(f"unknown command {cmd!r}; see --help")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
