---
description: 跑 wf-lint 檢查文檔（壞連結 / 超標檔 / >1 KB 條列 / 資料檔壞連結 / 查詢指令殘留 / 佔位殘留 / inbox 堆積）
---

> 本檔是 **Claude Code 的 slash 指令適配層（可選）**：其他 agent 工具沒有對應機制就忽略 `.claude/`，直接跑 `tools/wf-lint.sh`。

找到 `wf-lint.sh`（標準 `tools/wf-lint.sh`；非侵入式 `<wf 子資料夾>/tools/wf-lint.sh`），對專案根執行：

```
bash <路徑>/wf-lint.sh $ARGUMENTS .
```

回報 `BROKEN` 清單與各項計數。有 `BROKEN` 就修連結；殘留的佔位符與模板段表示導入未完成（`--strict` 會讓殘留算失敗）。`BIGLIST` 表示同質記錄表（非連結表）條列 >1 KB，抽成資料檔；`BIGLIST-LINKS` 只是連結表超過十條的提醒（永遠只 warning，不影響結束碼），該不該抽看是給人導航（留 md）還是給 AI 消化（抽資料檔），見 [data-files](../../wf/workflows/common/data-files.md)。`QUERYCMD` 表示 md 裡還留著資料檔查詢指令或工具路徑，照 [data-files](../../wf/workflows/common/data-files.md)「md 端留什麼」拿掉。`--strict` 時 oversize / biglist（同質記錄表）/ querycmd / 殘留都算失敗。
`BROKEN-ANCHOR` 表示 md 連結的 `#錨點`在目標檔找不到（拆檔／改標題最常斷這個），計入 `BROKEN`，一定要修。
