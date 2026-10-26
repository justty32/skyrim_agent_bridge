# INDEX — agent-bridge 專案地圖

agent-bridge = **Skyrim SE 的 SKSE plugin（C++，行程內 localhost HTTP server，port 5099）＋ Linux 端 `client/`（Python stdlib：`mo2ctl`／`bridge`／`qa_runner`／`qa_mcp`），讓 AI agent 結構化讀遊戲狀態、跑 console、操作對話與 MessageBox；是每次 QA 可獨立安裝／移除的 test harness，不進玩家 load order**。本檔只描述**頂層**：每列一句話＋連結；目錄內部複雜就放它自己的 README / INDEX。

## Repo 佈局

| 路徑 | 內容 |
|------|------|
| `src/` | SKSE DLL 原始碼（HTTP server、路由、State、GameActions、MessageBox；細節見 [README](../README.md)） |
| `client/` | Linux 端：`mo2ctl.py`、`bridge.py`、`qa_runner.py`、`qa_mcp.py`、`actor_value_probe.py` 與離線測試；[client/README.md](../client/README.md)、[QA-SCHEMA.md](../client/QA-SCHEMA.md) |
| `cmake/、ports/` | toolchain（clang-cl＋xwin）與 vcpkg overlay port（`commonlibsse-ng-fork`、`directxtk`） |
| `CMakeLists.txt、CMakePresets.json、vcpkg*.json` | 建置定義 |
| `README.md` | 人類向完整說明（路由表、設計筆記、build） |
| `workflows/` | 工作流（派發見 [WORKFLOWS.md](WORKFLOWS.md)；共享區 [workflows/common/](workflows/common/README.md)）|
| `.claude/commands/` | slash 指令適配層（可選）。Claude Code 為例：只讀專案根的這層，非侵入式佈局也留在根；沒有 slash 機制的工具忽略本目錄，直接跑 `tools/wf-lint.sh` |
| `tools/` | kernel 工具：`wf-lint.sh`（檢查）、`tabledb.py`（資料檔 CRUD／連結）、`find_big_lists.py`、`fix_moved_links.py`；非侵入式佈局在 `wf/tools/`；資料檔契約見 [workflows/common/data-files.md](workflows/common/data-files.md) |
<!-- wf-insert:INDEX -->

## 頂層文件

| 檔案 | 角色 |
|------|------|
| [WORKFLOWS.md](WORKFLOWS.md) | 派發器：意圖 → 工作流入口 |
| [STRUCTURE.md](STRUCTURE.md) | 結構整理參考（被動）：分層、膨脹即拆、四級成長、archive、工作流形式 |
| [SESSION-LOG.md](SESSION-LOG.md) | 我的 open 進度 |
| [WAIT_USER.md](../../../WAIT_USER.md) | 母 repo 的等使用者清單（本 repo 不另開）|
