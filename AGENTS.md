# agent-bridge — AI agent 專案備忘

agent-bridge = **Skyrim SE 的 SKSE plugin（C++，行程內 localhost HTTP server，port 5099）＋ Linux 端 `client/`（Python stdlib：`mo2ctl`／`bridge`／`qa_runner`／`qa_mcp`），讓 AI agent 結構化讀遊戲狀態、跑 console、操作對話與 MessageBox；是每次 QA 可獨立安裝／移除的 test harness，不進玩家 load order**。本檔是最頂層路由器，只指向下一層；細節不寫這裡（分層原則見 [STRUCTURE.md](wf/STRUCTURE.md)）。

## 開場與入口

- 每個 session 先跑 `grep -c '^- \[' wf/SESSION-LOG.md`：非 0 才開 [SESSION-LOG.md](wf/SESSION-LOG.md)（進度）。等使用者的事一律記母 repo [WAIT_USER.md](../../WAIT_USER.md)，本 repo 不另開一份。
- **碰原始碼前**：慣例與 code map → [workflows/common/conventions.md](wf/workflows/common/conventions.md)、[workflows/common/code-map.md](wf/workflows/common/code-map.md)；環境與指令 → [workflows/dev-env.md](wf/workflows/dev-env.md)。
<!-- wf-insert:AGENTS -->
- **要你動手做事** → [WORKFLOWS.md](wf/WORKFLOWS.md) 依意圖派發，再讀該工作流入口檔。
- **想看專案結構** → [INDEX.md](wf/INDEX.md)；人類向的完整說明 → [README.md](README.md)。
- 使用者偏好與邊界 → [workflows/common/user.md](wf/workflows/common/user.md)。

## 鐵律（always-on，隨時適用）

1. 重構 / 整理**不改原意**：開發＝行為不變且驗證綠燈（`python3 -m unittest discover -s client -p 'test*.py'`）；非開發＝原意不變，照 `Done when:` 驗收。
2. **不可逆或對外的動作**（push、刪除、對外送出、動 DB）要有**授權來源**：使用者當場確認，或他親自登記在清單裡（routines / schedule）。都沒有就先問。
3. **條列走資料檔、導航留 md**：給 AI 消化的表／清單 >1 KB 存 `.json`／`.csv`（契約 `wf-table/1`，見 [data-files](wf/workflows/common/data-files.md)），用 `wf/tools/tabledb.py` 讀寫、不整份讀進 context；給人點的導航連結留 md。
4. **具體流程**在各工作流入口檔，不在頂層。

## 專案摘要

C++23 SKSE DLL（CommonLibSSE-NG，clang-cl＋xwin＋vcpkg 交叉編譯）與 stdlib-only Python client 同 repo——port、`/state` 欄位名與讀它的 client 是同一份契約，雙端改動放同一個 commit。測試：`python3 -m unittest discover -s client -p 'test*.py'`（repo 根跑，122 項，不需遊戲）；DLL build 與遊戲內行為驗證見 [testing.md](wf/workflows/testing.md)、[dev-env.md](wf/workflows/dev-env.md)。兄弟 repo `scene-capture-bridge` 是 authoring tool，生命週期相反，不併。

<!-- wf-kernel v0.6+368468a (2026-10-26 導入) -->
