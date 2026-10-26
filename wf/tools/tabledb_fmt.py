#!/usr/bin/env python3
"""tabledb_fmt — 資料檔 json 裡 `$fmt` 路徑代號的展開（契約 wf-table/1 附錄）。

見 workflows/common/data-files-fmt.md。指示詞＝**恰好一個鍵 `$fmt`、值是字串**的物件，
可放在 rows 任一字串位置（cell 頂層或巢狀物件／陣列裡）與 meta `source`；
鍵有 `$fmt` 但不合這個外形 → 壞指示詞（`check` 算壞）。純字串裡的 `${…}` 是字面值、永不展開。

變數**名字是資料**：名字→算法（how）的對映在 fmt-vars.json（見 tabledb_fmt_vars），
算法實作在 tabledb_fmt_expand，本檔是對外門面。kernel 預設名字是 ${fileDirname} / ${gitRoot} / ${gitParent} / ${gitTop} /
${env:NAME}，實際有哪些以 `tabledb.py fmt --vars` 為準。算法：

  file-dir    本 json 所在目錄
  git-self    從 json 目錄往上第一個含 .git（檔或目錄）的目錄
  git-parent  從 git-self 再往上第一個；沒有就＝git-self
  git-top     往上最後一個；沒有上層就＝git-self
  env         namespace 型 ${prefix:NAME}：環境變數；不存在→錯誤、存在但空→空字串

純目錄往上找、不叫 git，所以被搬走（舊目錄已不存在）的檔也算得出舊值；
完全找不到 .git 時三個 git 算法退回 file-dir 並在 stderr 警告一次。展開一次，不再掃結果。

Python
  is_directive(v)            外形檢查
  context(json_dir)          **算法代號**→值 {"file-dir", "git-self", "git-parent", "git-top"}
  names(d) / canon(d)        名字→算法／算法→正式名；spec(d) 是合併後的變數表
  expand(template, ctx)      查名字表代換 ${…}；未知的 ${…} → FmtError
  expand_value(v, ctx)       走訪 cell → 展開後的等價值（指示詞換成字串，其他不動）
  parts(v, base)             走訪 cell → [{s, raw, top, error}]，raw 非 None 表示來自 $fmt
  directives(table)          CLI `tabledb.py fmt FILE` 的內容；cli(table, emit) 是它的 CLI 包裝
  value_of(v)                CLI `k=v` 的 v → 指示詞物件或字串
  vars_cli(path, emit)       CLI `tabledb.py fmt --vars [FILE]`
"""
import json
import os

import tabledb_fmt_vars as fv
from tabledb_fmt_expand import (  # noqa: F401  對外門面
    BAD, VAR, FmtError, canon, context, expand, is_directive, names, spec)


def parts(value, base, top=True):
    """cell 值（任意巢狀）→ [{"s", "raw", "top", "error"}]，照出現順序。

    s＝展開後的字串（失敗為 None）；raw＝原模板（不是 $fmt 就 None）；top＝是不是 cell 頂層值。
    只有真的遇到 $fmt 才去算 context（沒有指示詞的表不會找 .git、不會警告）。
    """
    if isinstance(value, dict) and "$fmt" in value:
        if not is_directive(value):
            return [{"s": None, "raw": json.dumps(value, ensure_ascii=False),
                     "top": top, "error": BAD}]
        raw = value["$fmt"]
        try:
            return [{"s": expand(raw, context(base)), "raw": raw, "top": top, "error": None}]
        except FmtError as e:
            return [{"s": None, "raw": raw, "top": top, "error": str(e)}]
    if isinstance(value, str):
        return [{"s": value, "raw": None, "top": top, "error": None}]
    out = []
    if isinstance(value, dict):
        for v in value.values():
            out += parts(v, base, False)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out += parts(v, base, False)
    return out


def expand_value(value, ctx, errors=None):
    """展開後的等價值；壞的收進 errors（沒給 errors 就 raise FmtError）。"""
    if isinstance(value, dict) and "$fmt" in value:
        try:
            if not is_directive(value):
                raise FmtError(BAD)
            return expand(value["$fmt"], ctx)
        except FmtError as e:
            if errors is None:
                raise
            errors.append({"raw": json.dumps(value, ensure_ascii=False), "error": str(e)})
            return value
    if isinstance(value, dict):
        return {k: expand_value(v, ctx, errors) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_value(v, ctx, errors) for v in value]
    return value


def directives(table, base=None):
    """每個 $fmt：[{index, column, raw, expanded}]；失敗的 expanded 為 null 並加 error。

    meta `source` 若是指示詞，以 {"index": null, "column": "source"} 列在最前面。
    """
    base = base or os.path.dirname(os.path.abspath(table.path))
    out = []

    def add(i, col, value):
        for p in parts(value, base):
            if p["raw"] is None:
                continue
            e = {"index": i, "column": col, "raw": p["raw"], "expanded": p["s"]}
            if p["error"]:
                e["error"] = p["error"]
            out.append(e)

    add(None, "source", table.meta.get("source"))
    for i, row in enumerate(table.rows):
        cols = list(table.columns) + [c for c in row if c not in table.columns]
        for col in cols:
            if col in row:
                add(i, col, row[col])
    return out


def value_of(v):
    """CLI `k=v` 的 v：恰好是合法 $fmt 指示詞的 JSON 才存成物件，其他（含別的 JSON）存字串。"""
    try:
        o = json.loads(v)
    except ValueError:
        return v
    return o if is_directive(o) else v


def cli(table, emit, rest=()):
    """`tabledb.py fmt FILE`：印每個指示詞；有 error 結束碼 1。`--vars` 改印變數表。"""
    if "--vars" in rest:
        return vars_cli(table.path, emit)
    es = directives(table)
    emit(es)
    return 1 if any("error" in e for e in es) else 0


def vars_cli(path, emit):
    """`tabledb.py fmt --vars [FILE]`：印合併後的變數表；FILE 給了就以那份 json 的 repo 為準。"""
    d = os.path.dirname(os.path.abspath(path)) if path else fv.HERE
    try:
        emit(spec(d))
    except (FmtError, OSError, ValueError) as e:
        emit({"error": str(e)})
        return 1
    return 0
