# SESSION-LOG — 進度（只列 open）

[INDEX](INDEX.md)｜等使用者的另記 [WAIT_USER](../../../WAIT_USER.md)

**寫入時機**：
1. **開始**多步驟工作前先寫一行 open（不是做完才寫）；硬中斷時本檔才有「進行中」。
2. **每次 commit 後**更新或刪除該行。
3. 條目格式：`- [工作流] 一句 open 狀態 → 下一步 / 連結`；完成即刪，歷史交給 git log，決策落到 [workflows/decisions.md](workflows/decisions.md)。

> 膨脹就拆：過大就開 `session_logs/` 按工作流拆檔＋index（照 [STRUCTURE](STRUCTURE.md)）；記成日誌時照 [STRUCTURE 日誌寫法](STRUCTURE.md#日誌寫法)。

## 最新進度

截圖 0.10.0 離線候選已接線；[契約與實機待驗](../docs/screenshot.md)承接，未部署。

NPC source 分拆與隔離驗證由
[重構紀錄](workflows/refactor/actor-diagnostics.md)承接；現役 DLL 與有限 live 範圍仍見
[候選紀錄](../docs/actor-diagnostics-build.md)，本次重構產物未部署。

## 各工作流 session-log

| 工作流 | session-log | open 摘要 |
|--------|-------------|----------|

