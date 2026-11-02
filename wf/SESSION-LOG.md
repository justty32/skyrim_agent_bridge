# SESSION-LOG — 進度（只列 open）

[INDEX](INDEX.md)｜等使用者的另記 [WAIT_USER](../../../WAIT_USER.md)

**寫入時機**：
1. **開始**多步驟工作前先寫一行 open（不是做完才寫）；硬中斷時本檔才有「進行中」。
2. **每次 commit 後**更新或刪除該行。
3. 條目格式：`- [工作流] 一句 open 狀態 → 下一步 / 連結`；完成即刪，歷史交給 git log，決策落到 [workflows/decisions.md](workflows/decisions.md)。

> 膨脹就拆：過大就開 `session_logs/` 按工作流拆檔＋index（照 [STRUCTURE](STRUCTURE.md)）；記成日誌時照 [STRUCTURE 日誌寫法](STRUCTURE.md#日誌寫法)。

## 最新進度

目前沒有可離線續行的 open 工作。單一 NPC v1 接線、隔離 build 與一名同 cell 已載入 NPC
的 live read 已完成；[已驗／未驗範圍](../docs/actor-diagnostics-build.md#live-驗證範圍2026-11-02)
由文件承接，後續部署與 runtime 管理由部署線管理。

## 各工作流 session-log

| 工作流 | session-log | open 摘要 |
|--------|-------------|----------|

