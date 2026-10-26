# conventions — 程式碼慣例（碰原始碼的工作流共用）

[common/README](README.md)｜[INDEX](../../INDEX.md)

碰原始碼的工作流（feature-dev / refactor / investigation）共用這套規矩：**寫碼時要遵守什麼**。哪個檔負責什麼領域、測試在哪 → [code-map](code-map.md)；真相層優先序也在 [code-map](code-map.md)。結構整理原則 → [STRUCTURE](../../STRUCTURE.md)。


## 慣例

| 項目 | 規矩 |
|------|------|
| 兩端契約 | port 5099、`/state` 欄位名、route 改動：DLL 與 `client/bridge.py` 同一 commit 改，並更新 [README](../../../README.md) 的 Route 表 |
| 網路邊界 | 只准 `INADDR_LOOPBACK`，不得 `INADDR_ANY`（此服務可執行 console command）：`grep -n INADDR src/HttpServer.cpp` |
| 禁止 hook | 不得 hook `ConsoleLog::VPrint`（與其他 console plugin 的 prologue detour 互蓋會 crash，詳見 README Pitfall） |
| 標頭順序 | `src/PCH.h` 必須先 `RE/Skyrim.h` 再 socket headers（`MAX_PATH` 巨集衝突） |
| 相依 | 新增 C++ dependency 必須通過 clang-cl＋lld-link＋xwin 交叉編譯；client 維持 stdlib only、不用 venv |
| 建置 | `cmake --fresh` 不可省略（裸名 `clang-cl` 的 cache 衝突） |
