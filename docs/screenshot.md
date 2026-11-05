# 截圖 v1 候選

[文件入口](README.md)｜[API](api.md)｜[client](../client/README.md)

0.10.0 已完成離線與一次有限實機驗證；未驗範圍見下方紀錄。`POST /screenshot` 接收空 body 或 `{}`，
不接受參數；成功回 `image/bmp`，失敗維持 JSON `ok:false,error`。只擷取目前遊戲
swapchain backbuffer，不讀桌面、不搶焦點。

成功是 54-byte header、top-down、32-bit BI_RGB BMP，像素為 BGRA、alpha 255；
接受單 sample RGBA8/BGRA8（UNORM/SRGB），尺寸最多 3840×2160，response cap 34 MiB。
`Content-Length` 是實際 bytes；`X-Screenshot-Request`、`X-Screenshot-Frame`、
`X-Screenshot-Width`、`X-Screenshot-Height` 提供請求、擷取 callback 序號及尺寸。
frame 是 render callback 序號，不宣稱等同 DXGI Present 次數。

HTTP thread 等待下一個可擷取 render callback，最多兩秒；忙碌、未安裝 hook、最小化、
無 frame、unsupported texture、device／surface 改變、Map 失敗皆回 503。每次請求獨立，
取消後的晚到結果不能滿足下一請求，也不回傳之前成功的舊圖。

## 使用

```sh
python3 -B client/screenshot.py /tmp/new-frame.bmp --timeout 5
```

需要已載入候選 DLL 的遊戲。存檔入口拒絕覆蓋既有路徑；失敗 exit 1。
`bridge.screenshot(timeout=5.0)` 成功回 `ok,bmp,request_id,frame,width,height`；`bmp` 是 owned bytes。
client 驗 MIME、單一 Content-Length、容量、完整 BMP layout 與 metadata；總 deadline 涵蓋慢速
headers/body。只接受 numeric loopback；`localhost` 直接映射到 127.0.0.1，避免 DNS 等待。

## Render phase 與所有權

`src/ScreenshotHook.cpp` 在 plugin load 安裝兩個已知 RenderUI callsite，僅支援
Skyrim 1.5.97／1.6.1170，VR／其他版本 fail closed。原 call 必須是指向可執行記憶體的 E8；
各自保存 predecessor、恰呼叫一次並保留返回值。沒有改全域或 instance COM vtable，也沒有
新增 SKSE Menu Framework runtime 依賴。現有 framework 若先安裝則沿其前鏈；若後安裝，
正常保存前鏈的 framework 沿本插件回呼；這時 CopyResource 在 framework overlay render 前，
不保證包含該 overlay。未知形式的前置 patch 直接拒絕。

來源是官方 [Hooks.cpp 固定 commit](https://github.com/QTR-Modding/SKSE-Menu-Framework-3/blob/59a14ce192d63022e9174adf3da8c317b5f3091e/src/Hooks.cpp)：
`RenderUIHook::install` 定義兩組 IDs／offset；`thunk1/thunk2` 在原 call 後進入 DX11 render。
該檔 SHA256 `5bd52ce21f2ac08dc08780335e65fa567ce426b3032d2d7f730c12637288bfc9`。
此為 callsite／phase 的 primary 依據；overlay 包含範圍、其他插件安裝順序與 Proton/DXVK
像素正確性仍須 runtime 驗證。

D3D adapter 每次從目前 Renderer window 取得 swapchain／device／backbuffer。
CopyResource 後只保留自己 staging 與 device COM refs，backbuffer 在同 callback 釋放；
後續 callback 用 DO_NOT_WAIT Map，按 RowPitch 複製，RAII Unmap。尺寸／format／device／
swapchain 改變取消 pending；timeout／Stop 不在 HTTP thread 操作 immediate context。
同 chain／尺寸／format 的 backbuffer 重建沒有 generation 通知，可能回本請求在重建前
已 copy 的 frame；不能宣稱此種 resize 已取消，需實機辨識影響。

Hook 隨 DLL 住到 process 結束，Stop 只停服務與取消請求，不做不安全卸載。

## 離線與實機界線

host 測試直接驗 BMP encoder、readback pipeline／request service；HTTP loopback 使用
production transport，client 使用 stdlib 真 loopback。命令見 [testing](../wf/workflows/testing.md)。
本機 build backup 僅保留前一個 build DLL，並不代表現役已部署 DLL 或可用 live rollback。

完整五項 runtime 驗收範圍（本輪只覆蓋部分情境）：

1. 兩次改鏡頭擷取，尺寸／frame／hash改變且目視符合 HUD 與場景。
2. 最小化／無新 frame 在 deadline 內回503，恢復後新請求成功，沒有舊圖。
3. resize／device／讀回錯誤期間拒絕錯配，恢復可再次擷取。
4. client 中斷與連續20次擷取後遊戲／ping存活、COM/GPU資源沒有持續增長。
5. 與現役 SKSE Menu Framework／其他 render hook 共存，predecessor 次數／返回保持，
   確認 overlay 範圍與 Proton/DXVK 色彩、黑畫面及效能。

### 2026-11-05 有限實機紀錄

本機 Skyrim 1.6.1170／Proton 環境，fresh SKSE 載入0.10.0與SKSE Menu Framework、
`/ping`與`/state`均可用。主選單及單一室內場景擷取1920×1080 BMP成功；使用者確認兩次
不同鏡頭的方向、色彩與HUD相符，request與callback識別及像素hash均不同。

主選單連續20次擷取20/20成功；client中斷後端點仍存活。process RSS前後增加約16MiB，
只是一段觀察，不是COM/GPU無洩漏證明。使用者回報最小化後仍有新render callback與新BMP，
因此未重現無frame timeout；不得說此項實機503已驗。

使用者恢復遊戲後，新request與新callback的1920×1080擷取再次成功；`/ping`／`/state`仍存活，
本輪無新增crash，原profile內容保持不變，遊戲留給使用者繼續操作。

同尺寸重建／裝置故障／Map失敗、所有hook安裝順序及overlay、長時間GPU資源行為均未完整實機驗證。
本輪DLL SHA256為`65ff23d0aca9b5d4fc16d3f52ba15f34dae629813964621e28e68c8f8d1eadd5`，
matching程式來源為`d5601b7`；本節為後續文件紀錄，不變更該候選binary。
