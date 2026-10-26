# data-files — 資料檔契約 `wf-table/1`（>1 KB 條列走 .json / .csv）

[common](README.md)｜[STRUCTURE](../../STRUCTURE.md)｜整理流程 [tidy](../tidy/README.md)

md 裡**每列同一組欄位的條列式區塊 > 1 KB**（表格、清單）抽成資料檔，md 只留摘要；讀寫一律走 `tools/tabledb.py`（非侵入式佈局在 `wf/tools/`），**不整份讀進 context**。本檔是格式與工具的唯一契約，工具實作照這裡做。

**考慮使用者——給人點的留 md、給 AI 讀的進資料檔。** md 裡的連結是給人點的捷徑，轉成資料檔就失去這個方便。所以**連結表不看 1 KB**：條列式連結**超過十條**才開始考慮，而且先看用途——**給人導航的**（README 路由表、目錄表、派發表）留 md，走一般 8 KB 上限，超了就**分層**（上層 README 只列子層入口，細目往下一層推）；**給 AI 消化的**（候選表、ledger、證據表、訊息集裡的連結欄）才抽資料檔，因為連結數量會拖累 AI 的判斷。

## 檔案格式

**JSON**（預設）：

```json
{"contract": "wf-table/1", "source": "README.md", "extracted": "2026-08-30",
 "columns": ["id", "name", "doc_path"], "link_columns": ["doc_path"],
 "rows": [{"id": "1", "name": "…", "doc_path": "notes/a.md#用法"}]}
```

- `contract` 固定 `wf-table/1`；`source`＝抽出自哪份 md（**相對本檔**）；`extracted`＝`YYYY-MM-DD`。
- `columns` 是欄位順序；`rows` 每筆同一組欄位，**缺的欄位視為空字串**。值是字串（可多行）或巢狀物件；原表怎麼寫就怎麼存（粗體、行內 code、連結原樣），不清洗。
- `link_columns`：哪些欄位的值是連結（可省略，見下）。
- 舊檔沒有 `contract` 也要能開（工具不拒讀，只是 lint 不掃它）。

**CSV**：第一列欄位名；只給**扁平、無多行值**的表；沒地方放 `link_columns`，改用**欄位名結尾 `_path` 或 `_link`** 表示該欄是連結。

## 連結

- cell 裡的連結兩種寫法都合法：markdown 連結 `[label](rel/path.md#anchor)`、或**裸相對路徑** `rel/path.md`。
- 一律**相對於資料檔所在目錄**解析（不是相對 `source`、不是相對 repo 根）；`#anchor` 解析時去掉；`http(s):`／`mailto:` 不算。
- 哪些算連結：所有欄位都掃 `[..](..)`；**連結欄**（`link_columns` 列出的、加上名字結尾 `_path`／`_link` 的）另外把裸值整個當路徑。`link_columns` 沒列就只有後者。
- 指向 `archive/` 的連結一律不該存在（封存規則見 STRUCTURE）；搬檔時工具不重寫、只列出，由人拿掉。
- **json 內跨兩層以上的路徑可用代號**：值寫成 `{"$fmt": "${gitRoot}/docs/x.md#錨"}`，讀取時展開；變數集合、展開規則與工具行為見 [data-files-fmt](data-files-fmt.md)。md 連結與 `.csv` 不用代號。

## md 端留什麼

抽完後原 md **留在原路徑**（外部連結都指它），內容只剩：**目的**一句、`已抽到 [x.json](x.json)（N 列）`、**欄位說明**（每欄一行）、**統計**（幾列、各狀態幾件），加上原本表以外的散文。**不留表的副本**——同一份東西不同時存在 md 與資料檔。

**不寫查詢指令、不寫工具路徑。** agent 照本契約就知道資料檔一律走 `tabledb.py`，md 再貼一次 `python3 …/tools/tabledb.py x.json` 只是雜訊；人要查法看本檔。需要指路時最多一句「查法見工作流的資料檔說明」，**不寫檔案路徑、不寫指令**。`wf-lint.sh` 的 `QUERYCMD` 會掃出殘留。

## 索引檔 `index.json`（可選）

只用在 **AI 要批次處理的清單**（逐件盤點、逐件核對），不是給人導航的目錄。欄位固定 `path`（相對本檔）、`title`、`summary`、`status`（可空），`link_columns: ["path"]`。給人看的導航表留 md，表前一行放 `<!-- wf-nav -->` 讓 lint 安靜（`AGENTS.md`／`WORKFLOWS.md`／`INDEX.md` 三個頂層路由器整檔免標）。

## 工具契約 `tools/tabledb.py`

索引 0-based；**所有輸出都是 JSON**。

| 指令 | 印什麼 |
|------|--------|
| `tabledb.py FILE` | `{file, contract, count, columns, …meta}` |
| `get I`／`find k=v…`／`grep RE`／`--slice A B`／`columns` | 該筆／符合的筆（含 `index`）|
| `add k=v…`／`update I k=v…`／`delete I` | 寫回後印該筆 |
| `links FILE` | 每筆每欄的連結：`[{index, column, target, resolved, exists}]`（`resolved` 是絕對路徑）|
| `check FILE` | 同上**只印壞的**（`exists=false`）；有壞的結束碼非 0 |
| `open FILE I [COL]` | `{path, content}`：該筆該欄連結指向的檔案內容；`COL` 省略就取該筆第一個連結 |
| `resolve FILE I [COL]` | 同上只印 `{index, column, target, resolved, exists}` |

Python 端：`from tabledb import load; t = load("x.json"); t.rows / t.get(i) / t.find(k=v) / t.add({...}) / t.update(i, k=v) / t.delete(i) / t.save()`。連結相關在 `tabledb_links.py`，CLI 入口仍是 `tabledb.py`。

## 其他工具怎麼對待資料檔

**`tools/find_big_lists.py [--min 1024] [--links-only] <path>…`** 列 md 裡超標的表／清單，每行附 `links=N` 與 `linked=all|some|none`（`all`＝每列都有連結＝連結表）；`--links-only` 改成列**連結 > 10 條**的區塊（不看 bytes），由人判斷用途；表前一行 `<!-- wf-nav -->` 豁免該區塊；清單項之間只隔一個空行仍算同一區塊（用空行切塊躲不掉門檻）。

**`tools/wf-lint.sh`** ① 呼叫 `find_big_lists.py --min 1024` 報 `BIGLIST`：同質記錄表（`linked` 不是 `all`）`--strict` 才算失敗；純連結表印 `BIGLIST-LINKS` 只 warning、永不算失敗。② 掃 repo 內所有 `.json`／`.csv`（`archive/` 除外）：有 `"contract": "wf-table/` 的 json 與所有 csv 跑 `tabledb.py check`，壞連結印 `BROKEN <檔>[<index>.<column>] -> <target>` 計入 `broken`。③ 呼叫 `check_anchors.py` 驗 md 連結的 `#錨點`（heading slug 與顯式 `id=`）在目標檔是否存在，不存在印 `BROKEN-ANCHOR <檔>:<行> -> <目標>#<錨點>` 計入 `broken`。④ 掃 md 裡殘留的查詢指令：任一行含 `tabledb.py` 且含 `python3 ` 或 `tools/tabledb.py` 就印 `QUERYCMD <檔>:<行>`（`archive/`、`wf/`、`AGENTS.md` 與本契約檔免掃）；平時只是 warning，`--strict` 才算失敗。

**`tools/fix_moved_links.py [--apply] [--root DIR] [--prefix SUB] moves.tsv`** 搬檔後照 `舊<TAB>新` 重寫 md 與資料檔（json 字串值、csv cell）裡的連結；**檔案本身被搬**時，其內連結以舊位置解析、以新位置重算。流程見 [tidy](../tidy/README.md)。
