# Three things that are not obvious

[手冊入口](README.md)；下列 CLI 範例以 `client/` 為工作目錄。

**Profile 換行必須保留。** `modlist.txt` 與 `loadorder.txt` 是 CRLF，`plugins.txt` 是 LF。
對 CRLF 內容直接用 `^+Name$` 比對可能無聲失敗；讀寫時必須保留每檔原本的換行格式。

**MO2 執行中會覆寫外部編輯。** MO2 將 profile 保存在記憶體，退出或切換 profile 時寫回
`modlist.txt`／`plugins.txt`。所有 mutating subcommand 因此在 MO2 或遊戲執行時拒絕操作；
`--force` 可覆寫檢查，但不會讓 MO2 知道外部變更。

**Process detection 必須精確比對 `argv[0]` 的執行檔名。** Proton launcher 的參數會提及
`ModOrganizer.exe`，不能用整條 command line 的 substring 判斷。`SkyrimSELauncher.exe`
及 Steam／Proton wrapper 可能比遊戲活得久，也不能算成 `SkyrimSE.exe`。
直接讀 `/proc` 並略過自身 PID；`pkill -f <pattern>` 可能匹配呼叫端 shell 自己的 command line。

## The cell name that wasn't

`ModForgeNavmeshNoop.esp` 覆寫 Bannered Mare 的 `CELL 0x0001605E`（90206 decimal），
沒有 `EDID` subrecord。Runtime cell 採 winning record 的 EditorID，因此名稱可能空白，
而 FormID、interior flag 與 actor list 仍正確。

[QA-SCHEMA.md](../QA-SCHEMA.md) 的現行判準：

- **Assert `cell_form_id`，不用 `cell`。** 任何未帶 EDID 的 CELL override 都能清空 EditorID；
  FormID 才是 engine identity。
- **ModForge 的 CELL override 未保留 EDID 是 generator 的缺陷。** Navmesh-only edit 不應使 cell 失去名稱。
