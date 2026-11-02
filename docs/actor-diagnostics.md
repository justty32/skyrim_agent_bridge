# 單一 NPC 唯讀診斷契約 v1

[文件入口](README.md)｜[現役 HTTP API](api.md)

**狀態：DLL producer、GET route 與 v1 capability 已實作並完成離線 Windows x64 build；尚未部署，live QA 尚未執行。**
舊 0.9.0 DLL 的 `/ping` 不含能力，既有 client 仍回 `unsupported`，不呼叫新 route。
新候選 DLL 以 capability 協商；版本字串不作能力判斷。

Done when: v1 唯讀 snapshot／route／能力協商、必要正反與缺欄位測試、隔離 PE build、
獨立 review、文件及停機部署／rollback 對齊。離線成果不等於遊戲驗收。

## 查詢與協商

Python 入口為 `bridge.actor_diagnostics(form_id=..., scope="cell")`。只接受單一 runtime
NPC reference FormID，不接受名稱、base ID 或玩家 `0x14`；字串是十六進位（可帶 `0x`），
整數是數值，皆須介於 1 與 `0xFFFFFFFF`，不接受 bool、空白或截斷解析。
與現有 actor routes 一致，`scope` 只接受 `cell` 或 `loaded`，預設 `cell`。

先 `GET /ping`，要求 `ok: true`、`plugin: "AgentBridge"`，並要求新增能力欄位：

```json
{"ok": true, "plugin": "AgentBridge", "version": "future", "capabilities": {"actor_diagnostics": [1]}}
```

版本協商只看能力清單，不以 plugin 版本字串猜測。沒有能力欄位、沒有該 key，或清單不含
`1`，一律 `unsupported`；能力格式錯誤是 `invalid_response`。不得呼叫 console 作 fallback。
只有協商成功才送：

```text
GET /actor/diagnostics?form_id=0x01001234&scope=cell&schema_version=1
```

DLL 在 game thread 取得同一份 snapshot；沿用 `GameActions.cpp` 的 FormID
selector 與 actor identity 慣例，但不呼叫移動／activate。`loaded` 的 FormID 路徑沿用既有
known-reference 查找，並不保證 3D／process 可讀。找不到 actor、不是 NPC、scope 不符或
整體查詢失敗皆為失敗回覆；不得偽造成所有手部為空。只有 route 讀值實作可用後才宣告能力。
不支援的 schema 回 `{"ok":false,"status":"unsupported","error":"..."}`；其他查詢失敗回
`{"ok":false,"status":"query_failed","error":"..."}`。沿現有 route 的 HTTP 400／503
錯誤格式亦可，client 將缺 `status` 的 `ok:false,error` 視為 `query_failed`。
若宣告能力卻遇 404，client 仍明確失敗，不推斷為合法空值。

## 成功回覆與欄位狀態

完整合成範例見 [actor-diagnostics-v1.json](../client/fixtures/actor-diagnostics-v1.json)。
所有成功回覆須有 `ok: true`、整數 `schema_version: 1`、`actor`、`equipped`、
`selected_spells` 與 `combat_style`；未知的附加 metadata 會保留，但必填讀值不能省略。

`actor` 至少含字串 `name`、整數 `form_id`、整數 `base_form_id`。兩個 ID 皆須非零 uint32，
`actor.form_id` 必須等於請求的 runtime reference；base ID 不拿來作 reference 身分比對。
這與現有 `StateActors.cpp`／`GameActions.cpp` 的命名一致。

每個讀值是一個有明確 `status` 的物件：

- `{"status":"value","value":{...}}`：有有效 form，不能附 `reason`。
- `{"status":"empty","value":null}`：已成功讀取且該欄位合法地沒有 form，不能附 `reason`。
- `{"status":"unavailable","reason":"actor_process_unavailable"}`：讀取前提不成立，必須有
  非空理由且不能有 `value`。理由是診斷文字，不是目前已實作的 DLL 錯誤列舉。

有值的 form 沿用 `State.cpp::FormRef` 的 `name`、`editor_id`、整數 `form_id`，另加
四字元 record signature `form_type`（例如 `WEAP`／`SPEL`／`CSTY`）。名稱與 EDID 的空字串
合法；form ID 的 0／null／bool 不合法。缺欄位、未知 status 或互相矛盾的狀態都屬
`invalid_response`，不能降級為 `empty`。

`ok: true` 只表示 snapshot 合法；某些甚至全部讀值仍可是 `unavailable`。呼叫端必須檢查
所需欄位的 status。transport、503、actor 查找或整體讀取例外則為 `ok: false` 的
`query_failed`；本地參數不合法是 `invalid_request`。所有失敗回覆保留 `error` 文字，沒有
裝備結論。缺 process 等已知讀取前提不成立可用欄位 `unavailable`；意外讀取錯誤不得吞掉。

## 讀值語義與已核來源

固定依賴版本由 [portfile.cmake](../ports/commonlibsse-ng-fork/portfile.cmake) 的
`alandtse/CommonLibVR@8593e41c1f780b5b16b227f4f62e574d01e0ac3d` 定義。下列是接線來源，
尚未證明在本機 load order 的可取得性：

`equipped.right`／`equipped.left` 沿用現有 `State.cpp` 的 `GetEquippedObject(false/true)`，
是手部持有 form，可能是武器、法術、盾或其他 form；不含護甲清單，也不從空值推成徒手行為。

`selected_spells.right`／`selected_spells.left` 是
`Actor::GetActorRuntimeData().selectedSpells[SlotTypes::kRightHand/kLeftHand]`。
固定版 [Actor.h](https://github.com/alandtse/CommonLibVR/blob/8593e41c1f780b5b16b227f4f62e574d01e0ac3d/include/RE/A/Actor.h#L150)
定義 left=0、right=1；同檔 L694 是 `MagicItem*` 陣列，L724 是 runtime accessor。
它與 `GetEquippedObject` 是獨立讀值，不能由有武器推斷法術槽空白；也不代表正在施法、已知
法術清單或 POTC 的私有角色／能力池。`MagicItem` 不預先假定只有 `SPEL`，保留實際 signature。

`combat_style.base` 是目前 actor 的 TESNPC base 上的 live `combatStyle` 欄位。
固定版 [TESNPC.h](https://github.com/alandtse/CommonLibVR/blob/8593e41c1f780b5b16b227f4f62e574d01e0ac3d/include/RE/T/TESNPC.h#L225)
的 getter 回該欄位，L273 定義成員；它不是磁碟 plugin 的 winner，亦不還原 template 來源。

`combat_style.reference_override` 是該 reference 的 `ExtraCombatStyle::combatStyle`，見
固定版 [ExtraCombatStyle.h](https://github.com/alandtse/CommonLibVR/blob/8593e41c1f780b5b16b227f4f62e574d01e0ac3d/include/RE/E/ExtraCombatStyle.h#L24)。
成功檢查 extra 缺席或其中 pointer 為 null 時可回 `empty`；若不能安全檢查則回 `unavailable`。
兩欄有值皆須為 `CSTY`。本契約不定義 `effective`、不推斷引擎選用優先序，不承諾其持久性。

## 離線使用與家中接線

在 repo 根目錄執行：

```bash
python3 client/actor_diagnostics.py --ref 0x01001234 --snapshot client/fixtures/actor-diagnostics-v1.json
python3 -m unittest discover -s client -p 'test*.py'
```

CLI 成功 exit 0，身分不符／格式錯／查詢失敗 exit 1。省略 `--snapshot` 才會協商 live
bridge；這個選項對沒有能力欄位的舊 DLL 得到 `unsupported`。合成 fixture 不包含玩家或私有 NPC 證據。

C++ 入口為 `GameActions::ActorDiagnostics`（沿用 `FindActor` 的單一 FormID 查找及
`ActorRef` 身分欄位），`Routes.cpp::ActorDiagnostics` 只在 game-thread task 中取得 pointer 與
snapshot，socket thread 不保存 actor pointer。`currentProcess` 缺席時，雙手與 selected spells
保守回 `unavailable: actor_process_unavailable`；base／extra CSTY 仍各自讀取。
`selectedSpells` 本身位於 actor runtime，此 guard 是保守讀值策略，未聲稱陣列一定不可讀。
未預期例外／zero FormID／不合法 signature 使整體查詢失敗。

新增的 host test 以 RE doubles 執行原始 producer 函式，並把實際輸出交既有 Python parser，
涵蓋 value／empty／unavailable、左右獨立、CSTY 兩層、lookup／base／metadata 失敗與缺欄位；
不是 engine 行為測試。真 CommonLib 型別由 Windows build 驗證：

```bash
python3 -m unittest discover -s tests -v
```

此測試使用既有 host `g++` 與預設 build 內的 nlohmann headers，不安裝依賴。
隔離候選 build、PE 與停機部署回退見 [候選驗證紀錄](actor-diagnostics-build.md)。
下次獲授權並停機部署後，才可做 live QA：指定單一 NPC，對照左右手／法術槽，驗合法空槽、
不可讀狀態、CSTY base／override、找不到 reference 及 game-thread timeout；保存原始 snapshot
與觀察。此次未呼叫 HTTP、未設定 NPC、未部署、未啟停遊戲、未碰 profile／DB。
