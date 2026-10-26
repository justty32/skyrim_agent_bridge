# common — 跨工作流共享（入口）

[INDEX](../../INDEX.md)

各工作流共用的東西。

<!-- wf-nav -->
| 路徑 | 內容 | 來源 |
|------|------|------|
| [gotchas.md](gotchas.md) | 共通踩坑（第二次撞到才記）| kernel |
| [user.md](user.md) | 使用者偏好、確認邊界、分支慣例 | kernel |
| [reply-style.md](reply-style.md) | 對話回覆風格：先講結論、一步一動作、何時可以破例 | kernel |
| `glossary.md`（長出來才建）| 領域詞彙 `詞 \| 意思 \| 別名`；常猜錯的術語多才建 | kernel |
| [data-files.md](data-files.md) | 資料檔契約 `wf-table/1`：給 AI 消化的條列 >1 KB 抽 `.json`／`.csv`、`tabledb.py` 讀寫；給人導航的連結表留 md | kernel |
| [data-files-fmt.md](data-files-fmt.md) | json 值裡跨層路徑用 `$fmt` 代號展開，省 `../../../` | kernel |
| [conventions.md](conventions.md) | 程式碼慣例；碰原始碼的工作流共用 | dev 包 |
| [code-map.md](code-map.md) | 程式碼導航 index：`領域 \| 檔案 \| 職責 \| 測試在哪` ＋ 維護鏈 | dev 包 |
<!-- wf-insert:COMMON -->

> 過時文檔封存進 `common/archive/`（見 [STRUCTURE](../../STRUCTURE.md)）。
