# WORKFLOWS — 工作流派發器

[INDEX](INDEX.md)｜結構 [STRUCTURE](STRUCTURE.md)

使用者要做事 → **從派發表選工作流 → 讀它的入口檔**；細節都在入口檔。

**可以跳流程**：單行或小範圍、低風險、不跨 session 的修正；純查詢或一次性回答，不留 durable 知識；使用者明確要求快速處理；既有工作流只會增加同步成本而不降低風險。跳流程不等於跳過工程規矩——仍要讀必要上下文、不破壞使用者改動、能測就測。

## 派發表

### 開發 flavor

碰原始碼的工作流共用 [common/conventions](workflows/common/conventions.md)（寫碼慣例）與 [common/code-map](workflows/common/code-map.md)（哪個檔負責什麼）。

| 觸發（你說…）| 工作流 | 入口檔（先讀這個）|
|--------------|--------|-------------------|
| 「開發 / 修改某個功能」「**修 bug**」 | **feature-dev** | [workflows/feature-dev/README.md](workflows/feature-dev/README.md) |
| 「跑測試 / 驗證」「這樣改有沒有壞」 | **testing** | [workflows/testing.md](workflows/testing.md) |
| 「重構 / 拆檔 / 整理結構」（行為不變）| **refactor** | [workflows/refactor/README.md](workflows/refactor/README.md) |
| 「搬檔案 / 改目錄名 / 拆 repo」 | **refactor**（搬移專章）| [workflows/refactor/moving-things.md](workflows/refactor/moving-things.md) |
| 「查清楚這是怎麼運作的」「這樣做可不可行」 | **investigation** | [workflows/investigation.md](workflows/investigation.md) |
| 「環境怎麼裝」「fresh clone 後要做什麼」「指令是什麼」 | **dev-env** | [workflows/dev-env.md](workflows/dev-env.md) |
| 「討論方案」「寫動工計畫」（spec / plan）| **planning**（kernel 管線的後兩段）| [workflows/planning.md](workflows/planning.md) |

<!-- wf-insert:WORKFLOWS -->

### kernel 內建

| 觸發（你說…）| 工作流 | 入口檔 |
|--------------|--------|--------|
| 「記 / 查踩坑」 | **gotchas** | [workflows/common/gotchas.md](workflows/common/gotchas.md) |
| 「整理 X」「封存過時的」「檔案太多／太雜」「太大要拆」「只留結論」 | **tidy** | [workflows/tidy/README.md](workflows/tidy/README.md) |
| 「記個想法」「以後要做」「排進 roadmap」「幫我規劃」 | **planning** | [workflows/planning.md](workflows/planning.md) |
| 「記個決定」「為什麼選 A 不選 B」 | **decisions** | [workflows/decisions.md](workflows/decisions.md) |
| 「我的偏好是…」「以後直接做 / 先問」 | **user** | [workflows/common/user.md](workflows/common/user.md) |

**都不符 → 看 [INDEX.md](INDEX.md)**。新開工作流 → 複製 [workflows/TEMPLATE.workflow.md](workflows/TEMPLATE.workflow.md) 並在上表加一列。要定期喚醒合 heartbeat 包、多 agent 協作合 multi-agent 包（都在模板 repo 的 `flavors/`）。

## 活狀態記哪裡（只列 open，完成即刪）

| 在等誰 | 記哪裡 |
|--------|--------|
| 等**使用者**做 / 驗證 / 決定 | **母 repo** [WAIT_USER.md](../../../WAIT_USER.md)（本 repo 不另開一份）|
| 等**同 repo 另一個 session / fork** | [SESSION-LOG.md](SESSION-LOG.md) 一行 open |
| 等**別資料夾的 agent** | 信件軸：multi-agent 包提供（派發表有 inbox 才有）|
