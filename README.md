# agent-bridge

這個 SKSE plugin 在 Skyrim 行程內開啟 localhost HTTP server，讓 Linux 端 agent 讀取結構化
遊戲狀態、執行 console、尋找與移動 actor、操作對話，全程不碰 OS 輸入或畫面層。視覺與
操作手感仍交由人工驗收；`POST /screenshot` 與 `POST /input` 尚未實作。

## 定位與邊界

`agent-bridge` 是每次 QA 可獨立安裝、移除的 **test harness**，不得進入玩家 load order；
`scene-capture-bridge` 則是由人以 hotkey 與 ImGui 操作、隨內容交付的 **authoring tool**。
兩者生命週期相反，因此保持為同層 repo；需要共用 scene-walking 邏輯時，將所需部分提取到
`agent-bridge`，不要把可執行 console command 的 listening socket 併入 authoring tool。
背景理由見 [`ai-ingame-qa-loop.md`](../../workflows/plans/ai-ingame-qa-loop.md) decision D1：
Wayland、非 XWayland 視窗與 Proton pressure-vessel 使螢幕擷取加模擬輸入不可靠。

## Status

目前版本為 0.9.0。current-cell、loaded-actor、cross-cell、retry、結構化 dialogue、結構化
MessageBox，以及 manifest + load-epoch baseline 路徑均已通過 runtime 驗證。
`/state.player.collision` 提供唯讀 character-controller diagnostics，包括 active Havok shape
tree、controller bounds 與 proxy contact margins。驗證值為：`bhkCharProxyController` capsule
radius `0.294322 m`、axis `1.118425 m`、total `1.707070 m`；完整 active-list AABB 為
`0.614350 × 0.844350 × 1.928800 m`，`keepDistance=0.050000 m`、
`keepContactTolerance=0.100000 m`。驗證用 DLL SHA-256：
`9c11b037a803357980946a09ef411038a91fee7f8937ca6e8fcf0141b0d2257c`.

| Route | Runs on | Notes |
|---|---|---|
| `GET /ping` | socket thread | 即使在 load screen 也回應，用來區分「process alive, game busy」與「process dead」。 |
| `GET /state` | game thread | `?include=nearby_actors,cell_actors,loaded_actors,inventory,quests,plugins&radius=&limit=`。`loaded_actors` 掃描四個 engine process list、去重，並回傳 cell/FormID/3D-loaded 狀態。`player` 與 `game` 永遠存在，其餘 opt-in。`equipped` **只含雙手**，護甲在 `inventory` 顯示為 `worn: true`。暫停或失焦時 task queue 可能不 drain 而回 503；liveness 請用 `/ping`，或以 background-active 模式啟動 client。 |
| `GET /global` | game thread | `?editor_id=...`；直接讀 live TESGlobal，不解析嘈雜的 console output。 |
| `POST /console` | game thread | `{"cmd": "...", "ref": "0x14"}`；`ref` 是選用的 console selected reference。Output 僅一行且為 best-effort。 |
| `POST /actor/move-to` | game thread | `{"name":"Falas Indaryn","scope":"loaded","distance":128}` 或 `{"form_id":"0x02001234"}`；`scope=loaded` 可搜尋 actor process lists 並跨 cell 移動。 |
| `POST /actor/activate` | game thread | 使用相同 actor selector；actor 載入玩家目前 cell 後啟動一般對話。 |
| `POST /dialogue/select` | game thread | 以 `text`、zero-based `index`、runtime `info_form_id` 三者之一選擇可見選項。 |
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
修正版 DLL SHA-256 為
`be09f146a2771f5c6e84f21be2f2bd3191eecaa8b685836ea751af04eb152051`；`/ping` 版本來自
CMake `PROJECT_VERSION`。

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

Linux 端位於 [`client/`](client/README.md)：`mo2ctl.py` 安裝、移除 mod 並啟動遊戲，
`qa_runner.py` 執行 [`qa.json`](client/QA-SCHEMA.md)，`qa_mcp.py` 暴露常用 MCP tools。
現役文件仍引用的階段報告保留於原路徑：

- [P1 Archive + FOMOD report](client/P1-ARCHIVE-FOMOD-REPORT.md)
- [P2 Profile Git report](client/P2-PROFILE-GIT-REPORT.md)
- [P3 Static Gates report](client/P3-STATIC-GATES-REPORT.md)

## Design notes

**Port 5099，只聽 loopback。** 必須使用 `INADDR_LOOPBACK`，不得使用 `INADDR_ANY`；此服務可
執行 console command，不能讓網路存取。Linux client 也 hardcode 5099，改 port 必須兩端同步。

**兩個 thread，一個 seam。** Accept loop 使用獨立 thread；幾乎所有 `RE::` read 只能在 game
main thread 安全執行。需要 game state 的 route 將 callable 交給 `GameThread::Run`，透過 SKSE
task interface marshal，預設 timeout 為 3 秒。Load screen 期間 task queue 可能完全不 drain；
timeout 回 503，由 runner retry，避免 handler 卡住 socket thread。

**Hand-rolled HTTP，不使用 cpp-httplib。** 介面只有少量 localhost JSON route 與單一 client；
新增 dependency 都必須通過 clang-cl + lld-link + xwin cross-compile。服務一次處理一個
connection，使用 `Connection: close`，request 上限 1 MiB。

**沒有 clean shutdown path。** SKSE 沒有 unload message，thread 活到 process 結束；
`Http::Stop()` 目前未使用。

## Pitfall: do not hook `ConsoleLog::VPrint`

**禁止 hook `ConsoleLog::VPrint`。** 在有其他 console plugin 的 load order 中，五-byte
prologue detour 會互相覆寫；曾於 Papyrus VM init 約 6.6 秒時 crash：

```
Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x000158B3D6AE
Access Violation: Tried to execute memory at 0x000158B3D6AE
[ 0][P] 0x000158B3D6AE
[ 1][S] 0x6FFFEA014404   AgentBridge.dll+0054404
[ 2][S] 0x6FFFE9819F94   ConsoleUtilSSE.dll+00B9F94
```

`write_branch<5>` 只保存被覆寫的 5 bytes；當 `MoreInformativeConsole.dll`、
`ConsoleUtilSSE.dll` 等 plugin 也 patch 同一位置時，保存的「原始」內容可能已是別人的半段
`jmp`。若需改善 output capture，依序選擇：讀更多 `ConsoleLog` state、使用已持有 hook 且有
API 的 plugin、hook 無人競爭的 call site；不得競爭 popular engine function 的 prologue。

目前做法是由 `Console::Execute` 寫入 sentinel、執行 command，再讀
`ConsoleLog::lastMessage`；若仍是 sentinel 就回空值。限制如下：

- **只保留一行。** `sqs` 與 `help` 只回最後一行。
- **Sentinel 只適用快速 command。** `load`、`coc` 仍可能混入其他 mod 的輸出，例如
  `GetInFaction >> 0.00` 或 `GetNumericPackageData >> 360.00`。

因此必須 **assert `/state`，不得 assert console output**。Output 只供診斷；
`output_captured: true` 不代表該行來自你的 command。

## Pitfall: `winsock2.h` goes *after* CommonLib, never before

一般 Windows 慣例是先 include `winsock2.h`，但本專案相反。CommonLibSSE-NG 自帶 Win32
re-declarations (`REX::W32`)，`REX/W32/BASE.h` 遇到真正 Windows header 會 hard-error：

```
error: Windows API detected. Please move any Windows API includes after CommonLib, or remove them.
```

`minwindef.h` 的 `#define MAX_PATH 260` 也會破壞後續
`inline constexpr auto MAX_PATH{260u}`。因此 `src/PCH.h` 必須先放 `RE/Skyrim.h`，再放 socket
headers；macro 只影響後續 parsing，而 `REX::W32` 名稱有 namespace，此順序安全。

## Build

Linux host 以 `clang-cl` cross-compile 成 Windows DLL。需要將 `xwin` splat 到
`~/.xwin-cache` 並設定 `VCPKG_ROOT`：

```bash
export VCPKG_ROOT="$HOME/dev/vcpkg" && cmake --preset build-release-clang-cl-linux && cmake --build build/release-clang-cl-linux
```

輸出：`build/release-clang-cl-linux/AgentBridge.dll`。

選用 auto-deploy：configure 前設定 `SKYRIM_MODS_FOLDER`（MO2 `mods/`）或
`SKYRIM_FOLDER`，post-build step 會將 DLL 放入 `SKSE/Plugins/`。

## Verifying it works

遊戲執行時：

```bash
curl -s 127.0.0.1:5099/ping && echo && curl -s 127.0.0.1:5099/state
```

`127.0.0.1` 可跨越 Proton boundary；plain wine 與 Proton 9 + pressure-vessel 均已驗證，
listening socket 屬於 container 內的 `wineserver`。
