# refactor — 重構 / 拆檔 / 整理結構

[WORKFLOWS](../../WORKFLOWS.md)｜[INDEX](../../INDEX.md)

只改結構、不改行為。方法在 [STRUCTURE](../../STRUCTURE.md)，本檔只管流程。

**何時用**：STRUCTURE 門檻超標（單檔過大、資料夾雜亂），或使用者要求整理。
**何時不用**：順手改了行為 → [feature-dev](../feature-dev/README.md)；還沒搞懂現況 → [investigation](../investigation.md)。

## Done when

- [testing](../testing.md) 的驗證指令綠燈，且與動手前結果相同。
- [common/code-map](../common/code-map.md) 中受影響領域的列已更新。

## 流程

1. 先跑驗證指令記下基準；紅的先修掉，別和重構混在一起。
2. 照 [STRUCTURE](../../STRUCTURE.md) 按職責拆／分類，不等分硬切。
3. 一次搬一塊，搬完就跑驗證。
4. 更新 code map 與所有指向舊路徑的連結。

## 內容

| 檔案 | 內容 |
|------|------|
| [moving-things.md](moving-things.md) | 搬檔／改目錄名／拆 repo：六類會斷的東西與驗證程序 |

## 交接

- 結構就位後要加功能 → [feature-dev](../feature-dev/README.md)；為什麼這樣拆 → [decisions](../decisions.md)。
