# 單一 NPC 唯讀診斷契約 v1

[文件入口](README.md)｜[現役 HTTP API](api.md)

**狀態：Python 契約／解析與合成離線測試已實作；DLL 尚未接線，沒有 live 讀值或遊戲驗證。**
現役 0.9.0 的 `/ping` 沒有本能力，client 會回 `unsupported`，不呼叫新 route。
本文件的 route 與能力欄位是家中 DLL 接線契約，不是現役 API 清單。

Done when: client 能協商 v1、驗證指定 NPC 的左右手與 CSTY 各項狀態，合成 fixture 與完整
client suite 通過，code map／文件對齊。本階段不含 C++、DLL build、部署或遊戲驗證。

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

未來 DLL 必須在 game thread 取得同一份 snapshot；沿用 `GameActions.cpp` 的 FormID
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
bridge；這個選項目前對現役 DLL 得到 `unsupported`。合成 fixture 不包含玩家或私有 NPC 證據。

家中下一步是實作 DLL read route／capability，先 build 與檢查 PE，再在授權的 runtime QA
讀單一 NPC：同時比較左右手物件／選用法術，確認合法空槽、不可讀狀態及 CSTY 兩層有別；
找不到 reference、無 game-thread 回應也須保留失敗狀態。記錄原始 snapshot 與實際觀察後
才能宣稱 live 支援。本輪不設定 NPC、不部署、不啟動遊戲、不碰 profile／DB。
