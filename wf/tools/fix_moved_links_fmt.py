#!/usr/bin/env python3
"""fix_moved_links_fmt — 搬檔時資料檔 cell 值（含 `$fmt` 路徑代號）的重寫。

CLI 入口在 fix_moved_links.py，這裡只放走訪與 `$fmt` 那半段。

`$fmt` 模板（見 workflows/common/data-files-fmt.md）以**舊位置**的變數值展開、remap，
再以**新位置**的**同一代號**寫回：`${var}/` + 相對該代號目錄的路徑。新目標不在該代號目錄下
（會出現 `../`）就改用包含它的最內層代號（gitRoot → gitParent → gitTop），都不包含就
`${fileDirname}/` + 相對本檔新目錄。寫回仍是 `{"$fmt": …}` 物件，模板裡不是連結的文字原樣保留。
namespace 型（`${env:…}` 這種帶冒號的）target 不重寫（環境變數不是搬檔能決定的）。
代號名字以該 json 的 fmt-vars.json 為準：raw 用別名時寫回保留別名，換代號時才用正式名。
"""
import os
import re

import tabledb_fmt

LINK = re.compile(r'(!?\[[^\]]*\]\()([^)\s]+)(\s+"[^"]*")?(\))')
VAR = re.compile(r"^\$\{(\w+)\}/?(.*)$")
ORDER = ("git-self", "git-parent", "git-top", "file-dir")
is_directive = tabledb_fmt.is_directive


def pick_var(new_abs, cn, prefer, vmap, canon):
    """寫回要用哪個代號：先試 raw 原本那個名字，不行就按算法找包含目標的最內層。

    cn＝新位置的 算法→值；vmap＝新位置的 名字→算法；canon＝新位置的 算法→正式名。
    """
    tries = []
    if vmap.get(prefer) in cn:
        tries.append((prefer, vmap[prefer]))
    tries += [(canon[h], h) for h in ORDER if h in canon]
    for name, how in tries:
        rel = os.path.relpath(new_abs, cn[how])
        if not rel.startswith(".."):
            return "${%s}/%s" % (name, rel)
    return "${%s}/%s" % (canon.get("file-dir", prefer),
                         os.path.relpath(new_abs, cn["file-dir"]))


class Rewriter:
    """把 fix_moved_links 的 remap／retarget／sub_md 接起來，走訪一個 cell 值。"""

    def __init__(self, remap, retarget, sub_md, archived, root):
        self.remap, self.retarget, self.sub_md = remap, retarget, sub_md
        self.archived, self.root = archived, root

    def _var_target(self, t, env, src):
        co, cn, vo, vn, canon = env
        path, frag = (t.split("#", 1) + [""])[:2]
        m = VAR.match(path)                     # 帶冒號的 namespace 型不會 match
        if not m or vo.get(m.group(1)) not in co:
            return None
        old_abs = os.path.normpath(os.path.join(co[vo[m.group(1)]], m.group(2)))
        new_abs = self.remap(old_abs) or old_abs
        if not os.path.exists(new_abs):
            return None
        if "/archive/" in new_abs + os.sep:
            self.archived.append((os.path.relpath(src, self.root), t))
            return None
        nt = pick_var(new_abs, cn, m.group(1), vn, canon) + ("#" + frag if frag else "")
        return None if nt == t else nt

    def _one(self, t, env, bo, bn, src):
        if t.startswith("${"):
            return self._var_target(t, env, src)
        return self.retarget(t, bo, bn, src)   # 沒用代號的照舊

    def template(self, raw, bare, bo, bn, src, edits):
        """一個 $fmt 模板 → 新模板；不必改回 None。"""
        env = (tabledb_fmt.context(bo), tabledb_fmt.context(bn), tabledb_fmt.names(bo),
               tabledb_fmt.names(bn), tabledb_fmt.canon(bn))
        if bare and "](" not in raw:            # 連結欄的裸值：整個模板當路徑
            nt = self._one(raw.strip(), env, bo, bn, src)
            if nt:
                edits.append((raw.strip(), nt))
            return nt

        def one(m):
            nt = self._one(m.group(2), env, bo, bn, src)
            if not nt:
                return m.group(0)
            edits.append((m.group(2), nt))
            return m.group(1) + nt + (m.group(3) or "") + m.group(4)

        new = LINK.sub(one, raw)
        return new if new != raw else None

    def value(self, v, bare, bo, bn, src, edits):
        """cell 值（字串／指示詞／任意巢狀）→ 重寫後的值。"""
        if is_directive(v):
            nt = self.template(v["$fmt"], bare, bo, bn, src, edits)
            return {"$fmt": nt} if nt else v
        if isinstance(v, dict):
            return {k: self.value(x, False, bo, bn, src, edits) for k, x in v.items()}
        if isinstance(v, list):
            return [self.value(x, False, bo, bn, src, edits) for x in v]
        if not isinstance(v, str):
            return v
        if bare and "](" not in v:
            nt = self.retarget(v.strip(), bo, bn, src)
            if nt:
                edits.append((v.strip(), nt))
            return nt or v
        return self.sub_md(v, bo, bn, src, edits)
