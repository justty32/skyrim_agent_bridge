#!/usr/bin/env python3
"""fix_moved_links — 搬檔後照 `舊<TAB>新` 重寫 md 與資料檔裡的相對連結。

usage: fix_moved_links.py [--apply] [--root DIR] [--prefix SUB] <moves.tsv> [...]

moves.tsv 每列 `<old>\t<new>`，相對 ROOT（前面放 `--prefix SUB` 就相對 ROOT/SUB，例如
submodule 自報的路徑）；old 是資料夾就當前綴搬移。ROOT 預設 `git rev-parse --show-toplevel`
（在 cwd 執行），`--root DIR` 覆寫。預設 dry-run，`--apply` 才寫。

掃描 `git ls-files -z --recurse-submodules`（不在 git 裡就退回 os.walk）的：
  *.md    行內連結 [..](target) / 圖片；fenced code block 內不動
  *.json  只有含 `"contract": "wf-table/` 的資料檔：遞迴走每個字串值裡的 [..](target)；
          連結欄（link_columns ∪ _path/_link 後綴）的裸值整個當路徑；`$fmt` 路徑代號以舊位置
          展開、以新位置的同一代號寫回（見 fix_moved_links_fmt.py 與 common/data-files-fmt.md）
  *.csv   每個 cell 同上；資料檔寫回用 tabledb 格式（indent=1）

掃描與搬移表在 fix_moved_links_scan，`$fmt` 那半段在 fix_moved_links_fmt。

**本檔自己被搬**時，其內連結以舊位置解析、目標若也搬了就 remap、再以新位置重算相對路徑。
指向 archive/ 的連結只列出、不重寫（要人拿掉）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fix_moved_links_fmt as fmtx  # noqa: E402
import fix_moved_links_scan as scan  # noqa: E402
import tabledb  # noqa: E402
import tabledb_links  # noqa: E402

LINK = fmtx.LINK
ROOT, RW = "", None
ARCHIVED = []


def retarget(target, bo, bn, src):
    """以 bo 解析、remap、再以 bn 算相對路徑；不必改就回 None。"""
    if not target or re.match(r'^[a-z]+:', target) or target.startswith("#"):
        return None
    path, frag = (target.split("#", 1) + [""])[:2]
    if not path:
        return None
    abs_t = os.path.normpath(os.path.join(bo, path))
    new_abs = scan.remap(abs_t)
    if new_abs is None:
        if bo == bn:
            return None
        new_abs = abs_t  # 本檔自己搬了：目標沒動但相對路徑要重算
    if not os.path.exists(new_abs):
        return None
    if "/archive/" in new_abs + os.sep:
        ARCHIVED.append((os.path.relpath(src, ROOT), target))
        return None
    new_rel = os.path.relpath(new_abs, bn) + ("#" + frag if frag else "")
    return None if new_rel == target else new_rel


def sub_md(text, bo, bn, path, edits):  # 重寫一段文字裡所有 [..](target)
    def one(m):
        nt = retarget(m.group(2), bo, bn, path)
        if not nt:
            return m.group(0)
        edits.append((m.group(2), nt))
        return m.group(1) + nt + (m.group(3) or "") + m.group(4)
    return LINK.sub(one, text)


def fix_md(path, bo, bn, edits):
    out, in_fence = [], False
    for line in open(path, encoding="utf-8").read().split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
        else:
            out.append(line if in_fence else sub_md(line, bo, bn, path, edits))
    return "\n".join(out)


def fix_table(path, bo, bn, edits, apply):
    t = tabledb.load(path)
    lcols = tabledb_links.link_columns(t)
    for row in t.rows:
        for k in list(row):
            row[k] = RW.value(row[k], k in lcols, bo, bn, path, edits)
    src = t.meta.get("source")  # source 也是相對本檔的路徑，可以是 $fmt 指示詞
    if isinstance(src, str) or fmtx.is_directive(src):
        new = RW.value(src, True, bo, bn, path, edits)
        if new != src:
            t.meta["source"] = new
    if edits and apply:
        t.save()


def main():
    global ROOT, RW
    apply, specs, prefix, root = False, [], None, None
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__); return 0
    while args:
        a = args.pop(0)
        if a == "--apply":
            apply = True
        elif a == "--root":
            root = args.pop(0)
        elif a == "--prefix":
            prefix = args.pop(0)
        else:
            specs.append((a, prefix)); prefix = None
    ROOT = os.path.abspath(root) if root else scan.git_root(os.getcwd())
    RW = fmtx.Rewriter(scan.remap, retarget, sub_md, ARCHIVED, ROOT)
    scan.load_moves(specs, ROOT)
    changed = 0
    for f in scan.files(ROOT):
        bn = os.path.dirname(f)
        bo = scan.old_dir_of(f)
        edits = []
        try:
            if f.endswith(".md"):
                new_text = fix_md(f, bo, bn, edits)
                if edits and apply:
                    open(f, "w", encoding="utf-8").write(new_text)
            else:
                fix_table(f, bo, bn, edits, apply)
        except (UnicodeDecodeError, OSError, ValueError):
            continue
        if edits:
            changed += 1
            print(f"{os.path.relpath(f, ROOT)}: {len(edits)}")
            for a, b in edits[:3]:
                print(f"    {a} -> {b}")
    print(f"files changed: {changed} ({'applied' if apply else 'dry-run'})")
    if ARCHIVED:
        print(f"\nLINKS TO ARCHIVED FILES (remove by hand): {len(ARCHIVED)}")
        for f, t in ARCHIVED:
            print(f"  {f}: {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
