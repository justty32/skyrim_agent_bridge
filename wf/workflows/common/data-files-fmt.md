# data-files-fmt — json 內路徑代號 `$fmt`（契約 `wf-table/1` 附錄）

[data-files](data-files.md)｜[common](README.md)

資料檔裡跨好幾層的 `../../../x.md` 對寫它的 agent 是負擔，所以 **`.json` 的值**可以改寫成帶代號的模板，讀取時才展開。**md 連結永遠不用代號**（給人點的必須是真相對路徑）；**`.csv` 不做**（cell 放不下物件）。值域擴充、舊檔照舊合法，契約名仍是 `wf-table/1`。

## 寫法（只有物件形式）

```json
{"id": "7", "doc_path": {"$fmt": "${gitTop}/instance/deployment-scope.md#範圍"},
 "note": {"$fmt": "見 [部署](${gitParent}/docs/deploy.md) 與 [鎖](${gitRoot}/docs/locks.md)"}}
```

- **指示詞**＝恰好一個鍵 `$fmt`、值是字串的物件；可放在 `rows` 任一字串位置（cell 頂層或巢狀物件／陣列裡）與 meta `source`。鍵不是 `$fmt`、或值不是字串 → 錯誤（`check` 算壞）。其他物件一律是普通巢狀值。
- **不做字串簡寫**：純字串裡的 `${gitRoot}` 是字面值、永遠不展開——字面值不必跳脫，也不會誤展開。
- 模板裡只有 `${名字}` 被代換；`$` 單獨出現是字面；未知的 `${…}` → 錯誤。展開一次、不再掃結果（變數值都是固定目錄，沒有巢狀與循環）。
- 展開後的字串照 [data-files](data-files.md#連結) 的既有規則抽連結（所有欄掃 `[..](..)`、連結欄裸值整個當路徑）；絕對路徑一律**轉成相對本 json 目錄**再判 exists，所以 `links`／`check`／`open`／`resolve` 行為不變，只多一個 `raw` 欄位（原模板）。

## 變數（名字由 `tools/fmt-vars.json` 定義，以 `tabledb.py fmt --vars` 輸出為準）

解析器**不寫死變數名**，只實作五個算法；名字→算法、說明、舊名（`aliases`，改名時保留相容）都從 **`tools/fmt-vars.json`**（契約 `wf-fmt-vars/1`，kernel-owned，隨 `wf-init` 複製）讀。專案要加自己的變數就放同目錄 **`fmt-vars.local.json`**（同格式，project-owned，kernel 升級不碰）：同名以 local 為準、多的追加。載入順序：json 所在 repo 根的 `wf/tools/` 或 `tools/`，找不到用腳本同目錄。

| `how` 算法 | 值 |
|------|----|
| `file-dir` | 本 json 所在目錄 |
| `git-self` | 從 json 目錄往上第一個含 `.git`（檔或目錄）的目錄（＝`git rev-parse --show-toplevel`）|
| `git-parent` | 從 `git-self` 再往上第一個含 `.git` 的目錄；沒有就＝`git-self` |
| `git-top` | 往上最後一個含 `.git` 的目錄；沒有上層就＝`git-self`（單層 repo 三者相同）|
| `env` | namespace 型 `${prefix:NAME}`：環境變數；**不存在 → 錯誤**、存在但空 → 空字串 |

當下快照（kernel 版 `fmt-vars.json`）：`${fileDirname}`＝file-dir、`${gitRoot}`＝git-self、`${gitParent}`＝git-parent、`${gitTop}`＝git-top、`${env:NAME}`＝env。本檔其他地方用這些名字只是舉例，**以工具輸出為準**。

找不到任何 `.git`：三個 git 變數都退回 `${fileDirname}`，stderr 警告一次。用純目錄往上找、不叫 `git`，所以被搬走（舊目錄已不存在）的檔也算得出舊值。

**VS Code 變數 ↔ 本契約**：沿用 `${camelCase}` 語法與 `${env:NAME}` 命名空間形式。`${fileDirname}` 同名同義；`${workspaceFolder}` 的「專案根」語意改成以 git 邊界定層級的 `${gitRoot}`／`${gitParent}`／`${gitTop}`。**砍掉**所有隨開啟目錄或當前檔案而變的：`${workspaceFolder}`／`${workspaceFolderBasename}`／`${cwd}`／`${file}`／`${relativeFile}`／`${relativeFileDirname}`／`${fileBasename*}`／`${fileExtname}`／`${fileWorkspaceFolder}`／`${lineNumber}`／`${selectedText}`／`${config:}`／`${command:}`／`${input:}`——資料檔進 git、任何人任何 cwd 讀到的值必須一樣。`${userHome}`／`${pathSeparator}` 不收（用 `${env:HOME}`；只有 `/`）。

## 建議條款

- json 內路徑**跨兩層以上（≥ 2 個 `../`）優先用 `$fmt`**；同目錄或一層內直接寫相對路徑。
- 代號選**包含目標的最內層**（`gitRoot` → `gitParent` → `gitTop`），代號後面不該再出現 `../`。
- md 連結永遠不用代號；`.csv` 不用。

## 工具行為

| 指令 | `$fmt` 怎麼處理 |
|------|----|
| `get`／`find`／`grep`／`--slice` | **原樣**回傳，不展開 |
| `add`／`update` | `k=v` 的 v 若是 `{"$fmt": "…"}` 這個合法指示詞 JSON 就存成物件，其他一律字串；Python API 給什麼存什麼 |
| `links`／`check`／`open`／`resolve` | 展開後抽連結；來自 `$fmt` 的項多 `raw`；展開失敗的項 `target`／`resolved` 為 null、`exists=false`、加 `error` |
| **`fmt FILE`** | 每個 `$fmt` 的 `[{index, column, raw, expanded}]`（meta `source` 以 `index: null` 列）；失敗加 `error` |

**`fix_moved_links.py`**：`$fmt` 值以**舊位置**的變數值展開、remap、再以**新位置**的**同一代號**寫回（`${var}/` + 相對該代號目錄）；新目標不在該代號目錄下就改用包含它的最內層代號，都沒有就 `${fileDirname}/相對路徑`。結果仍是 `$fmt` 物件。json 自己被搬到另一層 repo 時，`${gitRoot}` 等以新目錄重算。`find_big_lists.py`／`wf-lint.sh` 不動（後者靠 `check`）。

## 與 llmkit／aos 的對應

aos＝`~/repo/simple_tools/aos/core/inst`（`$env`／`$ref`／`$opt`）；llmkit＝memory-tools 規格的 `{"$ref": …}`。**一致的**：

| 項目 | aos／llmkit | 本契約 |
|------|----|----|
| 指示詞外形 | 單鍵物件、鍵 `$` 開頭、值必字串 | `{"$fmt": "…"}` 同形 |
| 字面值不跳脫 | 不用特殊字串冒充指示詞，未解析可原樣寫回 | 純字串永不展開、`get` 原樣 |
| 展開時機 | read → resolve → execute 分層 | 讀寫原樣，只有 links／check／open／resolve／fmt 展開 |
| 基準寫死 | `$ref` 相對呼叫端明示的 `base_path` | 相對 json 所在目錄，不藏在 cwd |
| 錨點 | llmkit `file.md#標題` | `#anchor` 同寫法 |

**不一致與理由**：

| 項目 | aos | 本契約 | 理由 |
|------|----|----|----|
| 不用 `$ref` | 取回另一份檔的值（JSON Pointer）| `$fmt` 只拼字串、不讀檔 | 語意不同不混用 |
| `$env` | 獨立指示詞 `{"$env":"X"}` | 行內 `${env:NAME}`；缺→錯誤、空→空字串照 aos | 要在同一條路徑裡拼接 |
| 未知 `$` 鍵 | 拒絕 | 只認 `$fmt`，其他是普通物件 | rows 本來就允許任意物件 |
| 巢狀／循環 | 取回值再解析、記身分防循環 | 展開一次不再掃 | 變數是固定目錄 |
| `$opt` | stderr 旗標 | 無 | 不需要 |
