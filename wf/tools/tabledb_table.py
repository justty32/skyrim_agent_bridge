#!/usr/bin/env python3
"""tabledb_table — 契約 wf-table/1 的資料模型（.json / .csv 的讀寫與 CRUD）。

CLI 與說明在 tabledb.py，這裡只放 Table 本身。外部一律 `from tabledb import load`，
不必直接 import 本檔；欄位、列、meta 的意義見 tabledb.py 的檔頭與
workflows/common/data-files.md。
"""
import csv
import json
import os
import re


class Table:
    def __init__(self, path, columns=None, rows=None, meta=None):
        self.path = path
        self.columns = list(columns or [])
        self.rows = list(rows or [])
        self.meta = dict(meta or {})

    # ---- read / write -------------------------------------------------
    @classmethod
    def load(cls, path):
        if path.endswith(".csv"):
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = [dict(r) for r in reader]
                return cls(path, reader.fieldnames or [], rows)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):  # bare list of dicts is tolerated
            data = {"rows": data}
        rows = data.get("rows", [])
        columns = data.get("columns") or (list(rows[0].keys()) if rows else [])
        meta = {k: v for k, v in data.items() if k not in ("rows", "columns")}
        return cls(path, columns, rows, meta)

    def save(self, path=None):
        path = path or self.path
        if path.endswith(".csv"):
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self.columns, extrasaction="ignore")
                w.writeheader()
                for r in self.rows:
                    w.writerow({c: r.get(c, "") for c in self.columns})
            return
        data = dict(self.meta)
        data["columns"] = self.columns
        data["rows"] = self.rows
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, path)

    # ---- CRUD ---------------------------------------------------------
    def get(self, i):
        return self.rows[i]

    def find(self, **kv):
        return [r for r in self.rows if all(str(r.get(k, "")) == str(v) for k, v in kv.items())]

    def find_indexed(self, **kv):
        return [(i, r) for i, r in enumerate(self.rows)
                if all(str(r.get(k, "")) == str(v) for k, v in kv.items())]

    def grep(self, pattern):
        rx = re.compile(pattern, re.I)
        return [(i, r) for i, r in enumerate(self.rows)
                if any(rx.search(str(v)) for v in r.values())]

    def add(self, row):
        full = {c: row.get(c, "") for c in self.columns}
        for k in row:
            if k not in self.columns:
                self.columns.append(k)
                full[k] = row[k]
        self.rows.append(full)
        return len(self.rows) - 1

    def update(self, i, **kv):
        for k in kv:
            if k not in self.columns:
                self.columns.append(k)
        self.rows[i].update(kv)
        return self.rows[i]

    def delete(self, i):
        return self.rows.pop(i)

    def __len__(self):
        return len(self.rows)
