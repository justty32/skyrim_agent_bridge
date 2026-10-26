#!/usr/bin/env python3
"""tabledb_fmt_vars — `$fmt` 變數表（fmt-vars.json，契約 `wf-fmt-vars/1`）的載入與合併。

**變數名字是資料、不是程式**：程式只實作算法（`how`），名字→算法的對映放在資料檔裡，
所以要改名／加別名／加變數只動 json。

  vars        [{name, how, doc, aliases}]；how ∈ file-dir / git-self / git-parent / git-top
  namespaces  [{prefix, how, doc}]；how ∈ env（`${prefix:ARG}` 形式）
  未知的 how → 載入時錯誤（訊息含檔名與 name）。

載入順序（cache 以 json 目錄為 key）
  1. json 所在 repo 根（git-self）底下的 `wf/tools/fmt-vars.json`，其次 `tools/fmt-vars.json`
  2. 都沒有就用**腳本同目錄**的 fmt-vars.json；再沒有就錯誤
  找到的那個目錄裡若有 `fmt-vars.local.json`（project-owned，同格式）就合併：同 name／prefix
  整筆以 local 為準，local 多出來的追加；每筆標 `source`（kernel／local）。

Python
  load(json_dir, git_self)   合併後的 {"vars", "namespaces", "files"}
  names(json_dir, git_self)  {名字或別名或 namespace prefix: how}
  canon(json_dir, git_self)  {how: 正式名}（同 how 有多筆取第一筆）
"""
import json
import os

KERNEL, LOCAL = "fmt-vars.json", "fmt-vars.local.json"
DIR_HOWS = ("file-dir", "git-self", "git-parent", "git-top")
NS_HOWS = ("env",)
HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE = {}


class FmtError(ValueError):
    """$fmt 變數表載入或模板展開失敗（是 ValueError，方便呼叫端一起接）。"""


def _read(path, source):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    for kind, key, hows in (("vars", "name", DIR_HOWS), ("namespaces", "prefix", NS_HOWS)):
        for e in d.get(kind) or []:
            if not isinstance(e.get(key), str) or not e[key]:
                raise FmtError("%s: %s entry without a %s" % (path, kind, key))
            if e.get("how") not in hows:
                raise FmtError("%s: %s %r has unknown how %r (want one of %s)"
                               % (path, kind, e[key], e.get("how"), "/".join(hows)))
            e["source"] = source
    return d


def _merge(base, over, key):
    out, idx = list(base), {}
    for i, e in enumerate(out):
        idx[e[key]] = i
    for e in over:
        if e[key] in idx:
            out[idx[e[key]]] = e
        else:
            out.append(e)
    return out


def _dir_for(git_self):
    for sub in (("wf", "tools"), ("tools",)):
        d = os.path.join(git_self, *sub)
        if os.path.exists(os.path.join(d, KERNEL)):
            return d
    return HERE


def load(json_dir, git_self):
    """合併後的變數表 {"vars", "namespaces", "files"}。"""
    if json_dir not in _CACHE:
        d = _dir_for(git_self)
        kf = os.path.join(d, KERNEL)
        if not os.path.exists(kf):
            raise FmtError("missing %s (looked under %s and in %s)" % (KERNEL, git_self, HERE))
        spec, files = _read(kf, "kernel"), [kf]
        lf = os.path.join(d, LOCAL)
        if os.path.exists(lf):
            loc = _read(lf, "local")
            spec["vars"] = _merge(spec.get("vars") or [], loc.get("vars") or [], "name")
            spec["namespaces"] = _merge(spec.get("namespaces") or [],
                                        loc.get("namespaces") or [], "prefix")
            files.append(lf)
        spec = {"vars": spec.get("vars") or [], "namespaces": spec.get("namespaces") or [],
                "files": files}
        nm, cn = {}, {}
        for v in spec["vars"]:
            cn.setdefault(v["how"], v["name"])
            for n in [v["name"]] + list(v.get("aliases") or []):
                nm[n] = v["how"]
        for n in spec["namespaces"]:
            nm.setdefault(n["prefix"], n["how"])
        _CACHE[json_dir] = (spec, nm, cn)
    return _CACHE[json_dir][0]


def names(json_dir, git_self):
    load(json_dir, git_self)
    return _CACHE[json_dir][1]


def canon(json_dir, git_self):
    load(json_dir, git_self)
    return _CACHE[json_dir][2]
