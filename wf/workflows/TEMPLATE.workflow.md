# <名稱> — <一句話：做什麼>

[WORKFLOWS](../WORKFLOWS.md)｜[INDEX](../INDEX.md)

> 複製本檔改寫成 `workflows/<名稱>.md`（單檔型）或 `workflows/<名稱>/README.md`（資料夾型，連結多一層 `../`）。段落順序固定，用不到的刪掉；改完在 WORKFLOWS.md 加一列。

<一句話：這工作流做什麼、產出什麼>

**何時用**：<觸發情境；派發表那句「你說…」的展開>
**何時不用**：<像是但不該進來的情境 → 該去哪個工作流>

## Done when

<可觀察的完成條件，只准三類：檔案存在、指令回傳、表格填滿。>
✗「功能做好」「使用者滿意」；✓「`<驗證指令>` 綠燈且 `docs/<x>.md` 有『用法』段」。

## 流程

1. <步驟>
2. <步驟>

## 內容（資料夾型才有）

| 檔案 | 內容 |
|------|------|
| `archive/`（長出來才建）| 過時文檔封存（見 [STRUCTURE](../STRUCTURE.md)）|

## 交接

- 完成後 → 接 <哪個工作流>；卡在使用者 → [WAIT_USER](../../../../WAIT_USER.md) 一行，跨 session → [SESSION-LOG](../SESSION-LOG.md) 一行，為什麼這樣選 → [decisions](decisions.md)。
