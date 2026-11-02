# SESSION-LOG — 進度（只列 open）

[INDEX](INDEX.md)｜等使用者的另記 [WAIT_USER](../../../WAIT_USER.md)

**寫入時機**：
1. **開始**多步驟工作前先寫一行 open（不是做完才寫）；硬中斷時本檔才有「進行中」。
2. **每次 commit 後**更新或刪除該行。
3. 條目格式：`- [工作流] 一句 open 狀態 → 下一步 / 連結`；完成即刪，歷史交給 git log，決策落到 [workflows/decisions.md](workflows/decisions.md)。

> 膨脹就拆：過大就開 `session_logs/` 按工作流拆檔＋index（照 [STRUCTURE](STRUCTURE.md)）；記成日誌時照 [STRUCTURE 日誌寫法](STRUCTURE.md#日誌寫法)。

## 最新進度

- [feature-dev] 單一 NPC 診斷的 v1 契約／Python 階段已完成（136 tests 通過）；DLL producer 尚未實作、現役回 `unsupported` → 家中環境續接 [最小接線與驗證](../docs/actor-diagnostics.md#離線使用與家中接線)，未部署／未驗遊戲。

## 各工作流 session-log

| 工作流 | session-log | open 摘要 |
|--------|-------------|----------|

