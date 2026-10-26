# tidy — 文件整理（封存過時、分類雜亂、抽資料檔、內容精簡）

[WORKFLOWS](../../WORKFLOWS.md)｜[INDEX](../../INDEX.md)

把文件層收乾淨：活文件只留現役與 open 的東西，過時的封存、雜亂的分類、指向被封存檔的連結清掉、過大的條列抽成資料檔、檔內的過往記錄刪掉只留結論。結構方法在 [STRUCTURE](../../STRUCTURE.md)（膨脹即拆／雜亂即分類／archive 規則／資料檔慣例）；本檔記的是**整理這件事怎麼跑一輪**：盤點 → 交接書 → 派工 → 核驗。

**何時用**：「幫我整理 X」；一個資料夾幾十個檔扁平混放；某份表格被歷史項塞到幾十 KB；log／inbox 堆了一堆沒消化；文件自述「歷史」「已被取代」卻還被活文件連著；檔內堆著決策過程、改過幾版、作廢方案。
**何時不用**：改程式碼結構（行為不變的搬檔／拆模組）→ refactor 工作流；只搬一兩個檔且不涉及封存或拆表 → 直接 `git mv`，不必走本流程；只是骨架版本升級（沒有封存或拆表）→ 不走本流程，照上游變動記錄手動套。

## 五條原則

1～4 的條文在 [STRUCTURE](../../STRUCTURE.md)（archive 規則、觸發 B、觸發 A-5、觸發 A-4），5 的細則在 [content-trim.md](content-trim.md)，本檔不重述，只記整理時實際要用的判準與掃描法：

<!-- wf-nav -->
1. **過時的就封存，連結當它不存在**——「過時」的判準：文件自述歷史／被取代／前提作廢；文件描述的目標（計畫、批次、環境配置）已不存在；結論已整份被某現役文件吸收。
2. **一個資料夾別擠太多檔**，同類檔案放鬆。
3. **小檔（<1 KB）太多就合併**。找法：`find <dir> -type f -size -1024c | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn`。
4. **給人點的留 md、給 AI 讀的進資料檔**；整檔 > 8 KB 另按語意拆資料夾，兩個門檻各自獨立觸發。格式契約與 A／B 兩種拆法在 [common/data-files.md](../common/data-files.md)。掃描：`tools/find_big_lists.py`、`find … -size +8192c`。
5. **檔內只留現行結論、寫精簡，使用者原文逐字留**：決策過程、改過幾版、為什麼這樣定、作廢方案、演變史都刪，靠 git 歷史找；日誌每日檔、報告、`archive/` 不適用。判準、原文認法、適用範圍與驗法在 [content-trim.md](content-trim.md)。

## Done when

- 目標資料夾根層只剩入口 README ＋ 語意子資料夾（或同類檔案集合），每個子資料夾有 `README.md`。
- 每個被封存檔：`grep -rn --include='*.md' '<檔名>' <repo> | grep -v archive/` → 0 行；`archive/README.md` 有它那一列。
- **可選**（預設不跑；擔心連結壞掉或使用者要求才跑）：`tools/wf-lint.sh --strict .` → `TOTAL broken=0 residue=0 oversize=0 biglist=0`（`biglist` 只算同質記錄表；連結表超標印 `BIGLIST-LINKS`，只 warning 不計入）。
- `find <repo> -name '*.md' -not -path '*/archive/*' -size +8192c | wc -l` → 0。
- 精簡過的檔：[content-trim](content-trim.md) 的原文檢查 → 0 行 `LOST`（被推翻而刪的列進報告）；報告列每檔精簡前後 bytes。
- 報告附 `moves.tsv`（`舊路徑<TAB>新路徑`）。
- 子資料夾 `README.md` ≤ 300 行、入口 `README.md` ≤ 100 行；名冊／登記表 ≤ 8192 bytes；`SESSION-LOG.md` ≤ 4096 bytes 且只列 open。

## 流程

角色分工：**調度者**只盤點、寫交接書、核驗、修跨資料夾／跨 repo 的連結；**實作線**（一個或多個工人）分類、切批、逐批驗收。線**不得**進交接書範圍以外的資料夾或 repo。線可以再分一層：一個範圍一條**管理線**，管理線再派**工人**（簡報範本 [manager-brief.md](manager-brief.md)）。

1. **盤點（調度者）**：
   ```bash
   find . -path ./.git -prune -o -type f -print | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn | head   # 哪個資料夾擠
   find <dir> -maxdepth 1 -type f -printf '%s %f\n' | sort -rn | head                                    # 哪個檔肥
   find <dir> -type f -size -1024c | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn                       # 哪個資料夾小檔太多
   python3 tools/find_big_lists.py --min 1024 <dir>                                                       # 哪個表／清單超標（含 links=N / linked= 判斷是不是連結表）
   git log --since=<兩週前日期> --oneline -- <檔>                                                          # 哪個檔沒人動（封存候選）
   grep -rlE --include='*.md' '<dir>/' <其他資料夾…> | wc -l                                                # 入站連結量：誰在依賴這個資料夾
   python3 tools/find_big_lists.py --links-only <dir>                                                      # 哪個連結表 > 10 條，要人判斷該留 md 還是抽資料檔
   ```
   把病灶寫成編號清單，直接放進交接書；不要讓線自己找範圍。
<!-- wf-nav -->
2. **交接書**固定寫：分類方案（類別數 5–7，一張表列每類收什麼）、**檔名一律不改**（外部連結靠檔名對映修）、一律 `git mv`、每個子資料夾要 README、封存規則與五條原則、禁區（不 commit／不進交接書以外的範圍／不刪／不碰 kernel 檔與 `AGENTS.md` 實質內容）、**固定條數**的驗收命令、報告要附 `moves.tsv`。
3. **線跑**：按檔案性質選 A／B 拆法（見 [common/data-files.md](../common/data-files.md)：A 法把同質大表抽成資料檔並在 md 端留摘要、對帳列數；B 法把過長散文按語意拆進同名資料夾，原路徑保留當入口）；共通：一律 `git mv`／新檔 `git add`，不留副本，不改內容實質，不重新判定任何狀態。多批可平行（各批只動自己範圍內的檔）。
   **內容精簡另開一條線**、跟搬檔拆檔分開 commit，照 [content-trim](content-trim.md) 做；它刪歷史但不改現行結論。
4. **核驗（調度者）**：不信報告，自己跑一次 Done when 的命令；看 `git diff --cached --name-status -M` 確認是 R（rename）不是 D+A；`archive/` 與 `AGENTS.md` 若被動到，只准是連結路徑那種修改。
5. **搬檔後修連結**：用各批的 `moves.tsv` 跑 `tools/fix_moved_links.py`（先 dry-run 再 `--apply`；它跳過 code fence、解析相對路徑後重算）。**指向被封存檔的連結不重寫**——照第 1 條原則拿掉。跨資料夾／跨 repo 的壞連結留給調度者收尾。
6. **收尾**：確認 Done when 全過，commit 由使用者或調度者決定。

## 踩過的坑

整理與拆檔實際踩過的坑集中在 [gotchas.md](gotchas.md)；動手前先讀一遍。

## 交接

- 完成後 → 若整理揭露了要裁示的事，[WAIT_USER](../../../../../WAIT_USER.md) 一行。
