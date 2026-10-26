# feature-dev — 功能開發 / 修 bug（工作流入口）

[WORKFLOWS](../../WORKFLOWS.md)｜[INDEX](../../INDEX.md)

改變行為的工作：加一個功能、修一個 bug，從動手到 commit。碰原始碼前先讀 [common/conventions](../common/conventions.md) 與 [common/code-map](../common/code-map.md)。

**何時用**：使用者說「我想開發／修改某個功能」「這裡壞了，修一下」——**修 bug 也走這條**，產出一樣是「行為改變＋驗證綠燈」。
**何時不用**：只是要查清楚原因、還不動碼 → [investigation](../investigation.md)；行為不變、只重排結構 → [refactor](../refactor/README.md)；還在討論要不要做、方案長怎樣 → [planning](../planning.md)。

## Done when

- [testing](../testing.md) 標「改完必跑」的驗證指令回傳 0。
- [common/code-map](../common/code-map.md) 中該領域的列已更新（新增／刪除的檔、職責變動、測試位置）。
- 要使用者實機驗證的在 [WAIT_USER](../../../../../WAIT_USER.md) 有一行；跨 session 沒收尾的在 [SESSION-LOG](../../SESSION-LOG.md) 有一行。

## 流程

```
讀 code map 找到相關領域（只讀清單裡的檔）
  → 增量修改（守 conventions）
  → 跑 testing 的自動驗證，綠燈
  → 要實機／實環境的交使用者驗證 → 回報問題 → 修 → 重複
  → 全數通過後：更新 code map → 補文檔 → commit
```

- 自動驗證是 agent 自己跑的把關；跑不了的一律交使用者（見 [testing](../testing.md) 的「誰跑」欄）。
- 迭代期間 code map / 文檔可暫時落後，**commit 前必須對齊**。
- 跨 session 時在本工作流 `session-log.md` 補一行 `[功能名] 文檔 / code map 待同步`，下個 session 才不會誤判已同步。

## 內容

| 檔案 | 內容 |
|------|------|
| `landed/`（長出來才建）| 已落地功能目錄：功能在哪、實作細節指標 |
| `gotchas.md`（長出來才建）| 本工作流專屬踩坑（共通的在 [common/gotchas](../common/gotchas.md)）|
| `session-log.md`（長出來才建）| 本工作流 open 進度（hub 在 [SESSION-LOG](../../SESSION-LOG.md)）|
| `archive/`（長出來才建）| 過時文檔封存（規則見 [STRUCTURE](../../STRUCTURE.md)）|


## 交接

- 驗證怎麼跑 → [testing](../testing.md)；改到一半發現該先整理結構 → [refactor](../refactor/README.md)。
- 為什麼選這個做法 → [decisions](../decisions.md)；卡在使用者 → [WAIT_USER](../../../../../WAIT_USER.md)。
