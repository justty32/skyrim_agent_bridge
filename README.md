# agent-bridge

這個 SKSE plugin 在 Skyrim 行程內開啟 localhost HTTP server，讓 Linux 端 agent 讀取結構化
遊戲狀態、執行 console、尋找與移動 actor、操作對話，不碰 OS 輸入。
0.10.0 加入遊戲 backbuffer [截圖候選](docs/screenshot.md)，尚待實機畫面驗收。它回本次 request 之後 copy 的畫面；同尺寸重建不保證取消，
`X-Screenshot-Frame` 是 render callback 識別，不是 Present counter。`POST /input` 尚未實作。

## 定位與邊界

`agent-bridge` 是每次 QA 可獨立安裝、移除的 **test harness**，不得進入玩家 load order；
`scene-capture-bridge` 則是由人以 hotkey 與 ImGui 操作、隨內容交付的 **authoring tool**。
兩者生命週期相反，因此保持為同層 repo；需要共用 scene-walking 邏輯時，將所需部分提取到
`agent-bridge`，不要把可執行 console command 的 listening socket 併入 authoring tool。

## Status

已驗證的既有功能版本為 0.9.0；0.10.0 是尚未部署的截圖候選。current-cell、loaded-actor、cross-cell、retry、結構化 dialogue、結構化
MessageBox，以及 manifest + load-epoch baseline 路徑均已通過 runtime 驗證。
`/state.player.collision` 提供唯讀 character-controller diagnostics，包括 active Havok shape
tree、controller bounds 與 proxy contact margins。驗證值為：`bhkCharProxyController` capsule
radius `0.294322 m`、axis `1.118425 m`、total `1.707070 m`；完整 active-list AABB 為
`0.614350 × 0.844350 × 1.928800 m`，`keepDistance=0.050000 m`、
`keepContactTolerance=0.100000 m`。

單一 NPC 的雙手裝備／選用法術與 CSTY 診斷已完成 [v1 DLL／Python 接線](docs/actor-diagnostics.md)與隔離 build，2026-11-02 已部署並通過一名同 cell 已載入 NPC 的 live read 驗證。舊 DLL 仍明確回 `unsupported`；[建置／live 證據與未驗範圍](docs/actor-diagnostics-build.md)保留完整界線。

技術文件見 [docs/](docs/README.md)，Linux 工具見 [client/](client/README.md)。
NPC 原始碼按 actor query／snapshot／HTTP handler 分工；[重構與未部署驗證紀錄](wf/workflows/refactor/actor-diagnostics.md)保留來源界線。

### Baseline load proof

詳見 [技術文件](docs/api.md#baseline-load-proof)。

### 結構化 MessageBox 與 actor 操作

詳見 [技術文件](docs/api.md#結構化-messagebox-與-actor-操作)。

## Design notes

詳見 [技術文件](docs/runtime-and-build.md#design-notes)。

## Pitfall: do not hook `ConsoleLog::VPrint`

詳見 [技術文件](docs/runtime-and-build.md#pitfall-do-not-hook-consolelogvprint)。

## Pitfall: `winsock2.h` goes *after* CommonLib, never before

詳見 [技術文件](docs/runtime-and-build.md#pitfall-winsock2h-goes-after-commonlib-never-before)。

## Build

詳見 [技術文件](docs/runtime-and-build.md#build)。

## Verifying it works

詳見 [技術文件](docs/runtime-and-build.md#verifying-it-works)。
