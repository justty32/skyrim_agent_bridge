# 原生 keyboard tap v1（0.11.0，已部署、首次 tap 被 guard 取消）

[文件入口](README.md)｜[API](api.md)｜[驗證命令](../wf/workflows/testing.md)

`POST /input` 接受 `{"schema":1,"action":"tap","scan_code":18}`，不接受 query、缺欄、
額外欄、bool、浮點 scan code 或未知 action。`scan_code` 是 DirectInput code，範圍1–254；
實際只接受鍵盤 deviceButtons 中存在、且最近game-task snapshot的 ControlMap 唯一對應 gameplay action 的鍵。
這涵蓋目前鍵位表所有符合條件的 keyboard action，並非固定幾個示範鍵。未綁定或多義鍵拒絕。
不支援 mouse、text、modifier chord、跨請求 hold、VR；僅開放1.5.97與1.6.1170。

client 先查 `/ping.capabilities.input: [1]`，舊 DLL 回 `unsupported` 而不 POST。
Python 呼叫 `bridge.input_tap(18)`；獨立 CLI：`python3 -B client/input.py --scan-code 18`。
既有QA入口提供MCP `qa_input_tap`及qa.json `input_tap` step；二者單次派送，失敗即停止，
不進semantic retry；request receipt保留以供判讀。MCP server重啟後才會發現新tool。
以上是API用法；2026-11-05稍晚已備份0.10.0並部署0.11.0，唯一一次live tap被guard取消，詳見下節。

## 派送、生命週期與來源

`src/Input.cpp` 在 SKSEPlugin_Load 早期 chain `BSWin32KeyboardDevice::Process` vfunc 02，
避免 input thread 啟動後 patch／原函式指標 publication 的窗口。DataLoaded 才啟用 service。
每次原 Process 回來後，`InputCore` 最多追加一顆 engine cache ButtonEvent；
原生 poll 隨後繼續 ControlMap mapping、既有 event-source dispatch、queue reset。
沒有自行呼 handler、`SendEvent`、console 等價操作或 OS `SendInput`。

原生相位 primary 是作者自己的
[MFO pinned RE 紀錄](https://github.com/marthofdoom/MFO/blob/3fb4106d731f76a6a51dbc00dde04f76c3fc8e8c/native/Board.cpp#L1881-L1927)：
兩個允許的 runtime 都先呼 devices 的 Process，再 mapping，最後 emit。這是外部作者
的反組譯證據，本輪沒有解碼本機 packed EXE，也不把 input thread 說成 game thread。

matching dependency 由 [portfile](../ports/commonlibsse-ng-fork/portfile.cmake) 鎖定；
[queue source](https://github.com/alandtse/CommonLibVR/blob/8593e41c1f780b5b16b227f4f62e574d01e0ac3d/src/RE/B/BSInputEventQueue.cpp)
的 `AddButtonEvent`、`PushOntoInputQueue`、`ClearInputQueue` 證明十顆固定 cache 與 reset 所有權。
該 fork 的4參數 wrapper 錯誤地解參考 flat runtime 的 VR extension；
`InputDispatch::Append` 保留同樣 flat cache 填值、Push、increment，略過該欄位。
事件從未 heap Create/free；cache 的建構、重用、銷毀始終歸 engine。

[ButtonEvent](https://github.com/alandtse/CommonLibVR/blob/8593e41c1f780b5b16b227f4f62e574d01e0ac3d/include/RE/B/ButtonEvent.h)
定義 down 為 value>0、duration=0，up 為 value=0、duration>0。
第一 poll 送 down，下一 poll 送 up（duration至少0.001秒），第三 poll 證明前一次原 poll 已返回，
才回 completed。不是同一串 linked list 內塞一對、不受 wall-clock sleep 排程影響。

## 邊界與結果

HTTP thread 只排 request／等至多2秒，不碰引擎。原生 input phase 驗 mapping、實體鍵未按住、
cache容量、前景 HWND 及 game-task 發布的 UI snapshot；snapshot超250ms拒新 down。
UI與256bit mapping snapshot在game-task持 engine UI mutex產生，input thread不遍歷ControlMap容器；MenuOpenClose epoch 使舊 snapshot立即失效，Probe與Emit各核
同一epoch。down保存epoch，menu開關即使很快恢復也不能把跨menu tap回completed。
目前只接受無menu context／cursor／modal、非暫停、主選單／載入／console／對話關閉的 gameplay。

單一 pending；busy回409。取消、Stop、deadline或input phase觀察到focus/menu不符，若發生在 down 前則取消；
down 後回 unknown 並保留 release debt與busy slot，之後 input phase必須嘗試up。
cache滿、缺少後續 poll或停止遊戲時不能保證立刻release；不清除debt、不回完成、不自動重送。
Stop 保留常駐hook，讓後續poll仍能送欠的up，同時保留截圖Stop callback。

回應含 `schema`、正整數 `request_id`、`state`、`reason`、`down_dispatched`、`up_dispatched`。
HTTP200只配completed；rejected為400（busy409、unavailable503），cancelled/unknown為503。
flags 是追加至原生 queue 的證據；只有completed還經過第三phase確認。結果不代表遊戲動作生效。
client 永遠回 `effect: unknown`；POST開始後的失聯、格式錯誤、deadline均unknown，絕不重試。

不改實體keyboard device state；down前拒相同實體鍵，但玩家若在之後同時按鍵，無法保證兩個
來源的release語義不交錯。驗收應獨佔輸入。其他mod吞事件、disabled controls、遊戲上下文
改變都可能令完整派送沒有預期效果，必須以後續狀態／畫面觀察驗證。

## 2026-11-05有限實機結果

0.11.0已停機備份後部署；來源commit `edc8d87`，DLL SHA256
`d0e93e5e8be6d1bdd4921a272e516bc61e3a50cd811f03c8414941ee0e51733c`。
22:21，使用者已進 `DLC2SVGreathall`、load epoch 1，交出控制並切回遊戲等待約5秒後，
唯一一次 `input_tap(scan_code=57)` 回HTTP503、`state: cancelled`、
`reason: environment_blocked`、down/up flags均false。當時目視為正常HUD／常駐overlay。
此結果只證guard拒絕了這次request；沒有down/up派送證據，跳躍與其他遊戲效果均未驗成。
本輪於此停止POST，沒有放寬guard、改code或重部署。

依 `src/InputCore.cpp` 的 `Service::Tick` 順序，這個reason證該次已進native Tick，
且mapping guard已通過，接著environment.allowed為false，在送down前取消；
physical-key與cache-capacity guards排在allowed之後，不能從這次結果判定它們是否成立。
`src/Input.cpp` 的Keyboard Probe把snapshot epoch、menuAllowed、snapshot age<250ms、
非空cached HWND及foreground相符等合成單一bool，末尾再核epoch。
因此無法從回應區分UI flags／暫停／特定menu、snapshot過期、epoch失配或foreground不符；
正常HUD畫面及等待5秒亦不能證明這些內部條件全部通過。UI snapshot目前保守拒絕任何
UsesMenuContext／UsesCursor／Modal menu，常駐overlay是否命中仍未知。

後續最小診斷方向是在既有結果中細分blocked reason並保存該次Probe的bounded證據：
UI snapshot時列出命中的menu名稱／flags、pause/application/item/modal／closing狀態；
input phase記snapshot age、snapshot／validated／current epoch、cached-window存在與foreground是否相符。
證據須沿既有game-task snapshot／atomic ownership傳遞，不在HTTP或input thread重讀UI容器。
先只診斷、保留guard與單次不重送語義；有新授權才新增診斷code或重跑live，不直接豁免HUD／overlay。

## 待實機驗證

離線host／client與matching Windows x64 build已完成；live目前只得到一次guard取消。後續取得授權並定位guard原因後，
先用已綁定的非破壞鍵驗down/up與一個可觀察效果，再核menu／失焦／loading拒絕、實體鍵衝突、
關閉與重新載入時release debt，最後重跑0.10.0截圖回歸。另一個允許的runtime亦須獨立實機核對；
不因host綠燈認為遊戲效果、Wine/DXVK/其他input hook相容性或無stuck key已驗。
