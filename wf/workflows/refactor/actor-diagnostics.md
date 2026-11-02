# NPC diagnostics：按責任分拆紀錄

[重構入口](README.md)｜[現行契約](../../../docs/actor-diagnostics.md)

2026-11-02 將共用 actor 查找／身分、唯讀 snapshot 與 HTTP handler 分開，沒有新增功能。
`GameActions::ActorDiagnostics` 公開入口、HTTP route、JSON、錯誤、selector、queue、thread 與
既有 timeout 全部維持原樣；`Routes.cpp` 繼續集中註冊 route 與宣告 ping capability。

## 責任與來源

`ActorQueries.*` 共用 actor identity／lookup／lookup error 與 selector case folding，
供既有 action／dialogue 和 diagnostics 使用；不複製或改寫查找政策。
`ActorDiagnostics.cpp` 只負責 v1 snapshot。`ActorDiagnosticsRoute.*` 只負責驗證請求並透過
原有 `GameThread::Run` 取得 snapshot。`RouteParsing.h` 保留全字串十六進位解析，
由診斷與既有 routes 共用。新 `.cpp`／header 均列在原有 CMake source declarations。

| Source | 原行數 | 分拆後行數 |
|---|---:|---:|
| `GameActions.cpp` | 352 | 201 |
| `Routes.cpp` | 325 | 293 |
| `ActorQueries.cpp` | — | 114 |
| `ActorDiagnostics.cpp` | — | 51 |
| `ActorDiagnosticsRoute.cpp` | — | 29 |
| `RouteParsing.h` | — | 23 |

不是將大檔平均切段；其餘 action、dialogue、console 與 state route 沒有擴大翻修。
九個搬移函式 body 與前一來源 commit 去除空白後完全一致；改動只有檔案、namespace／using
接線及 parser 的 inline linkage。原來源為 `ffdc3ed7cc9a74382618818dc0ca49c235d8f42e`。

## 離線驗證與未部署界線

基準與分拆後均為 136 client tests、1 native producer／route harness 通過。
extractor 現在直接讀新 `ActorDiagnostics.cpp`、`ActorDiagnosticsRoute.cpp` 及
`RouteParsing.h`，沒有繼續從舊檔擷取 body。對新 producer 路徑移除 process guard 的
負向突變成功使 harness 失敗，確認測試會執行新 production body。
獨立唯讀 review 核對函式內容、namespace、CMake 與 test extraction，沒有 blocking。

使用既有 clang-cl／lld-link／xwin 及已安裝的 vcpkg headers/libs；
`nice -n 19`、最多 2 jobs。未下載或安裝依賴、未用 Wine，沒有 runtime／GUI／部署操作。
隔離輸出 `build/actor-diagnostics-refactor-20261102/AgentBridge.dll` 成功，
`POST_BUILD = :`，沒有 auto-deploy copy。此 DLL **只作未部署的重構驗證**。
SHA-256：`38de914d6a8ac4235367f58fc62b27a872ecefb1d14bee7c720b46c9fe35bd04`。

PE 為 AMD64（`0x8664`）／PE32+（`0x20B`）；exports 仍為 `SKSEPlugin_Load`、
`SKSEPlugin_Query`、`SKSEPlugin_Version`；imports 仍為 `WS2_32.dll`、`KERNEL32.dll`、
`ole32.dll`、`VERSION.dll`、`USER32.dll`、`SHELL32.dll`。

已部署且有限 live 驗證的 DLL 仍是實作 commit
`8f011eb5030bf8ce2f09e4e254899805371e9280`、SHA-256
`aff80463c3de2da61317d4e4416019567e34bdf95b31ece18ea7b72ec3278b3d`；
原候選 DLL／zip／provenance 與 `docs/actor-diagnostics-build.md` 歷史證據未修改。
新來源 build 成功不會改寫其 live 驗證範圍，也不授權部署。

本機 ignored `build/actor-diagnostics-refactor-20261102/` 保存 baseline／後測 logs、
`configure.log`、`build.log`、`pe.txt`、`body-equivalence.json`、`mutation.log` 與
`provenance.json`。source commit 與逐檔 hash 由 provenance 記錄，不將 binary 放入版控。
