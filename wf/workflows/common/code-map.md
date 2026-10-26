# code-map — 程式碼導航 index（哪個檔負責什麼）

[common/README](README.md)｜[INDEX](../../INDEX.md)

碰原始碼前先查這張表，只讀相關領域列出的檔；動完再照維護鏈把表更新回去。寫碼慣例本身在 [conventions](conventions.md)。

## 領域表

<!-- wf-nav -->
| 領域 | 檔案 | 職責 | 測試在哪 |
|------|------|------|---------|
| HTTP／路由 | `src/HttpServer.*`、`src/Routes.*`、`src/GameThread.h` | 手寫 HTTP（loopback 5099）、route 分派、marshal 到 game thread | （無 C++ 測試；經 client 測試與遊戲內驗證） |
| 遊戲狀態 | `src/State*.cpp`、`src/GameActions.*`、`src/Console.*`、`src/MessageBox.*` | `/state`、actor 移動／啟動、console、結構化 MessageBox／dialogue | `client/test_bridge.py`（client 側契約） |
| MO2 入口／交易 | [mo2ctl.py](../../../client/mo2ctl.py) | CLI、原名稱 re-export、mutating commands 與 process control；monkeypatch 的名稱及呼叫鏈留同模組 | `client/test_mo2ctl.py`、`test_mo2ctl_launch.py`、`test_mo2ctl_checkpoint.py` |
| MO2 環境／profile | [mo2ctl_env.py](../../../client/mo2ctl_env.py)、[mo2ctl_files.py](../../../client/mo2ctl_files.py)、[mo2ctl_background.py](../../../client/mo2ctl_background.py)、[mo2ctl_manifest.py](../../../client/mo2ctl_manifest.py)、[mo2ctl_profile_lists.py](../../../client/mo2ctl_profile_lists.py)、[mo2ctl_profile_git.py](../../../client/mo2ctl_profile_git.py) | 環境、檔案、INI 還原、ledger／快照、清單、Git 語意 | 同上 |
| 安裝來源／FOMOD | [mo2ctl_archives.py](../../../client/mo2ctl_archives.py)、[mo2ctl_fomod_models.py](../../../client/mo2ctl_fomod_models.py)、[mo2ctl_fomod_parse.py](../../../client/mo2ctl_fomod_parse.py)、[mo2ctl_sources.py](../../../client/mo2ctl_sources.py) | 解壓、model／default choices、XML、來源 materialization | `client/test_mo2ctl.py` |
| 靜態驗證 | [mo2ctl_housecarl.py](../../../client/mo2ctl_housecarl.py)、[mo2ctl_static_gates.py](../../../client/mo2ctl_static_gates.py)、[mo2ctl_crash.py](../../../client/mo2ctl_crash.py) | stdio MCP、baseline 分類、crash triage | `client/test_mo2ctl.py` |
| QA 入口／執行 | [qa_runner.py](../../../client/qa_runner.py)、[qa_runner_common.py](../../../client/qa_runner_common.py)、[qa_runner_report.py](../../../client/qa_runner_report.py) | CLI、原名稱 re-export、Runner 與其 monkeypatch seam、共用結果型別、report | `client/test_qa_runner.py` |
| QA 契約／指紋 | [qa_runner_conditions.py](../../../client/qa_runner_conditions.py)、[qa_runner_baseline_rules.py](../../../client/qa_runner_baseline_rules.py)、[qa_runner_baseline.py](../../../client/qa_runner_baseline.py)、[qa_runner_fingerprint.py](../../../client/qa_runner_fingerprint.py)、[qa_runner_validation.py](../../../client/qa_runner_validation.py) | JSON paths、manifest 規則／preflight、epoch／指紋、離線驗證 | `client/test_qa_runner.py` |
| HTTP／MCP client | `client/bridge.py`、`client/qa_mcp.py`、`client/actor_value_probe.py` | HTTP client、MCP server、嚴格 actor value 讀取 | `client/test_bridge.py`、`test_qa_mcp.py`、`test_actor_value_probe.py` |


## 真相層優先序

各專案可以改自己的優先序，但必須明確。預設：

```text
code/tests > schema/examples/fixtures > code map > docs > generated
```

- 上層與下層衝突時，**以上層為準並修正下層**。
- generated（產生出來的檔、html）永遠不是唯一真相。
- 原始來源與摘要衝突時，以**原始來源**為準。
- code map 是**導航不是規格**；行為以 code/tests 為準。

## 維護鏈：程式碼 > code map > 文檔

**優先級**（衝突或時間不夠時，依序保持一致）：程式碼 > code map > 文檔。
**code map 與程式碼衝突時以程式碼為準，立刻改 code map。**

1. **修改前**：先讀本表找到相關領域，只讀清單裡的檔——不讀無關領域的檔。
2. **修改後**：新增／刪除了原始碼檔案，或某檔職責顯著改變，必須同步更新本表。
3. 原始碼裡**不加**「對應 code map」的註釋（維護成本過高）；反向查找直接 grep 本檔。
4. 迭代期間本表可暫時落後，**commit 前必須對齊**（見 [feature-dev](../feature-dev/README.md)）。
