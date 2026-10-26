# HTTP API 與 runtime 契約

[文件入口](README.md)

<!-- wf-nav -->
| Route | Runs on | Notes |
|---|---|---|
| `GET /ping` | socket thread | 即使在 load screen 也回應，用來區分「process alive, game busy」與「process dead」。 |
| `GET /state` | game thread | `?include=nearby_actors,cell_actors,loaded_actors,inventory,quests,plugins&radius=&limit=`。`loaded_actors` 掃描四個 engine process list、去重，並回傳 cell/FormID/3D-loaded 狀態。`player` 與 `game` 永遠存在，其餘 opt-in。`equipped` **只含雙手**，護甲在 `inventory` 顯示為 `worn: true`。暫停或失焦時 task queue 可能不 drain 而回 503；liveness 請用 `/ping`，或以 background-active 模式啟動 client。 |
| `GET /global` | game thread | `?editor_id=...`；直接讀 live TESGlobal，不解析嘈雜的 console output。 |
| `POST /console` | game thread | `{"cmd": "...", "ref": "0x14"}`；`ref` 是選用的 console selected reference。Output 僅一行且為 best-effort。 |
| `POST /actor/move-to` | game thread | `{"name":"Falas Indaryn","scope":"loaded","distance":128}` 或 `{"form_id":"0x02001234"}`；`scope=loaded` 可搜尋 actor process lists 並跨 cell 移動。 |
| `POST /actor/activate` | game thread | 使用相同 actor selector；actor 載入玩家目前 cell 後啟動一般對話。 |
| `POST /dialogue/select` | game thread | 以 `text`、zero-based `index`、runtime `info_form_id` 三者之一選擇可見選項；`contains` 只影響 `text` selector，預設 `false` 為 exact match。 |
| `POST /dialogue/close` | game thread | 結束目前玩家對話。 |
| `POST /messagebox/select` | game thread | `{"text":"OK","message":"Done Writing"}` 或 `{"index":0}`；選用 exact `message` guard 可防止 modal 內容改變後誤按。 |

### Baseline load proof

Raw API 以 `{"cmd": "load <save filename without extension>"}` 載入存檔，但非同步 command
被接受不代表載入成功。`load_baseline` 先驗證 deployment-owned manifest 中 `.ess`/`.skse`
pair 與 SHA-256，確認檔案位於所選 MO2 profile 的 local-saves 目錄，記錄
`game.load_epoch`，再要求 epoch 前進並輪詢 `/state` 的
player/cell/interior/dead/closed-MessageBox fingerprint。`qa_runner` 預設以
`mo2ctl launch --background-active` 暫時啟用 `bAlwaysActive`，kill 時還原原始 INI。

SKSE 的 `kPostLoadGame` payload 是 scalar `(void*)result`，`dataLen == 1`，不是可解參考的
`bool*`；把 success value `1` 當指標會在 `0x1` crash。Handler 必須轉成 `uintptr_t`，且只在
長度正好為 `sizeof(bool)`、scalar value 為 `1` 時前進 epoch。Compile-time checks 涵蓋
success、failure、malformed value 與 malformed length；upstream contract 見
[`skse64/Hooks_SaveLoad.cpp`](https://github.com/ianpatt/skse64/blob/master/skse64/Hooks_SaveLoad.cpp)。
`/ping` 版本來自 CMake `PROJECT_VERSION`。

`game.load_epoch` 只證明成功載入；若規格要求沒有延遲 modal，仍須在 load 後加入明確
observation window。`include=plugins` 回傳 engine 實際解析的 load order，應以此驗證安裝；
`plugins.txt` 只代表請求狀態。`index` 為 FormID 的實際 prefix：full plugin `0x00`–`0xFD`，
light plugin `0xFE000`+。

### 結構化 MessageBox 與 actor 操作

MessageBox 與 dialogue 都走 Skyrim 行程內的結構化 callback，不使用 `xdotool`、keyboard
event、mouse coordinate 或 generic focused-window 操作：

- `GET /state` 的 `game.message_box` 提供 `open`、`ready`、message text 與依顯示順序排列的
  buttons；Skyrim stock MessageBox 沒有獨立 title。
- `POST /messagebox/select` 接受一個 `text` 或 zero-based `index`；exact `message` guard
  讓 read-and-select race-safe。
- Python client 與 qa.json runner 提供 `select_message_box`，MCP 提供 `qa_message_box`；
  `qa_wait` / `assert_state` 可等待任一 `game.message_box` 欄位。
- Scaleform 結構缺失、message 不符、text 不存在或重複、index 無效時一律 fail closed，並列出
  可見選項。

實作以 `MessageButtons` array 找 active movie object，再 dispatch menu 註冊的 native
`buttonPress` callback；contract 來自 stock
[`MessageBox.as`](https://github.com/Mardoxx/skyrimui/blob/master/src/messagebox/MessageBox.as)
與 CommonLibSSE-NG `RE/M/MessageBoxMenu.h`。

Actor selector 預設 `scope=cell` 與 exact name；FormID selector 可消除同名歧義，
`scope=loaded` 搜尋四個 `ProcessLists` bucket。跨 cell `move_to` 先用 Skyrim native
reference-to-reference move，再套 requested standing offset。回傳物件含 `cell_form_id`、
`worldspace_form_id`、`loaded_3d`、`disabled`，可區分「known reference」與「ready to talk」。
實際 load order 能否解析 persistent unloaded reference 仍須 live QA；找不到時 API 明確回傳
not-found。`loaded_actors.distance` 只有在 `same_cell` 為 true 時具有幾何意義。

Linux 端位於 [`client/`](../client/README.md)：`mo2ctl.py` 安裝、移除 mod 並啟動遊戲，
`qa_runner.py` 執行 [`qa.json`](../client/QA-SCHEMA.md)，`qa_mcp.py` 暴露常用 MCP tools。
MCP 入口是 `client/qa_mcp.py`，被 agentctl 的 `mcp__skyrim-qa` 使用。
