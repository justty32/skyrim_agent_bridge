#!/usr/bin/env python3
"""find_big_lists — 找出 markdown 裡超過門檻的條列式區塊（表格或清單）。

用法: find_big_lists.py [--min 1024] [--links-only] [--exclude-dir archive ...]
                        [--exempt-file NAME ...] <path>...
輸出: 每個命中的區塊一行
      <bytes>\t<file>:<start>-<end>\t<table|list>\t<rows>\tlinks=<N>\tlinked=<all|some|none>
      區塊 = 連續的表格列（以 | 開頭）或連續的清單項（-、*、數字.），其他行即中斷；
      清單項之間只隔一個空行仍算同一區塊（用空行切塊躲不掉門檻）。
      code fence 內的內容不算。links=區塊裡 `](` 的總數；linked=all 表示每一列都含連結
      （表格排除表頭與 |---| 分隔列）＝這是張連結表，給人點的導航，通常該留在 md。

  --min           區塊 bytes 門檻（預設 1024，即契約的 1 KB）
  --links-only    改列「連結 > 10 條」的區塊，不看 bytes 門檻（--min 忽略），由人判斷用途
  --exempt-file   檔名等於 NAME 的整檔跳過（可重複；wf-lint 用它豁免頂層路由器）
  --exclude-dir   跳過的資料夾名（預設 archive / done / superseded / .git / node_modules，可重複；
                  放信的 inbox/ 一律跳過，workflows/inbox/ 是工作流文件照掃）

豁免：區塊**前一行**（中間可有空行）含 `<!-- wf-nav -->` 就跳過該區塊——導航表要留在
md 裡就靠這個標記；三個頂層路由器（AGENTS.md / WORKFLOWS.md / INDEX.md）整檔豁免。
"""
import os
import re
import sys

ROW = re.compile(r"^\s*\|")
SEP = re.compile(r"^\s*\|[\s|:\-]+$")
ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
CONT = re.compile(r"^\s{2,}\S")  # indented continuation of a list item
NAV = "<!-- wf-nav -->"


def scan(path, min_bytes, links_only=False):
    out = []
    try:
        lines = open(path, encoding="utf-8").read().split("\n")
    except (UnicodeDecodeError, OSError):
        return out
    kind, start, size, rows, fence = None, 0, 0, 0, False

    def nav_exempt():
        j = start - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        return j >= 0 and NAV in lines[j]

    def flush(end):
        if not kind or nav_exempt():
            return
        block = lines[start:end]
        links = sum(l.count("](") for l in block)
        body = [l for l in block if (ROW if kind == "table" else ITEM).match(l)]
        if kind == "table":
            body = [l for l in body if not SEP.match(l)][1:]
        linked = "none" if not links else \
                 "all" if body and all("](" in l for l in body) else "some"
        if links <= 10 if links_only else size < min_bytes:
            return
        out.append((size, f"{path}:{start + 1}-{end}", kind, rows, links, linked))

    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            flush(i); kind = None; fence = not fence; continue
        if fence:
            continue
        this = "table" if ROW.match(line) else "list" if ITEM.match(line) else None
        if this is None and kind == "list" and CONT.match(line):
            size += len(line.encode()) + 1; continue
        if this is None and kind == "list" and not line.strip() \
                and i + 1 < len(lines) and ITEM.match(lines[i + 1]):
            size += 1; continue  # 清單項之間單一空行不算分隔（契約：空行躲不掉門檻）
        if this != kind:
            flush(i)
            kind, start, size, rows = this, i, 0, 0
        if this:
            size += len(line.encode()) + 1
            rows += 1
    flush(len(lines))
    return out


def main(argv):
    min_bytes, links_only = 1024, False
    exclude, exempt, paths = {"archive", "done", "superseded", ".git", "node_modules"}, set(), []
    it = iter(argv)
    for a in it:
        if a == "--min":
            min_bytes = int(next(it))
        elif a == "--links-only":
            links_only = True
        elif a == "--exclude-dir":
            exclude.add(next(it))
        elif a == "--exempt-file":
            exempt.add(next(it))
        elif a in ("-h", "--help"):
            print(__doc__); return 0
        else:
            paths.append(a)
    if not paths:
        print(__doc__); return 0
    hits = []

    def take(p):
        if os.path.basename(p) not in exempt:
            hits.extend(scan(p, min_bytes, links_only))

    for p in paths:
        if os.path.isfile(p):
            take(p); continue
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d not in exclude
                       and not (d == "inbox" and os.path.basename(root) != "workflows")]
            for f in files:
                if f.endswith(".md"):
                    take(os.path.join(root, f))
    for size, loc, kind, rows, links, linked in sorted(hits, reverse=True):
        print(f"{size}\t{loc}\t{kind}\t{rows}\tlinks={links}\tlinked={linked}")
    why = "with > 10 links" if links_only else f">= {min_bytes} bytes"
    print(f"# {len(hits)} block(s) {why}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
