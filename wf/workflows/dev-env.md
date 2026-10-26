# dev-env — 開發環境、指令、外部工具

[WORKFLOWS](../WORKFLOWS.md)｜[INDEX](../INDEX.md)

這台機器上要能開發需要什麼、怎麼裝、跑什麼指令；外部工具設定與 env var 也收在這裡。

**何時用**：fresh clone、換機器、裝不起來、忘了指令、要加一個外部工具或環境變數。
**何時不用**：驗證 / 測試怎麼跑 → [testing](testing.md)；程式碼慣例 → [common/conventions](common/conventions.md)。

## Done when

- 照「流程（fresh clone 後）」走完，`python3 -m unittest discover -s client -p 'test*.py'` 回傳 0。
- 下面三張表沒有空欄；要使用者親自做的（帳號、授權、金鑰）在 [WAIT_USER](../../../../WAIT_USER.md) 各佔一行。

## 流程（fresh clone 後）

1. 取得原始碼（無 submodule；`ports/` overlay 已在 repo 內）。
2. 備好 toolchain：`clang-cl`、`cmake`、`ninja`、xwin splat 到 `~/.xwin-cache`、`VCPKG_ROOT`（本機是 `~/dev/vcpkg`）。只動 `client/` 時可略過這步。
3. client 不需安裝（stdlib only，無 venv）。
4. 跑一次冒煙指令：`python3 -m unittest discover -s client -p 'test*.py'`（回傳 0）。DLL 要另外 build 才驗得到。

## 指令表

| 做什麼 | 指令 | 備註 |
|--------|------|------|
| client 離線測試 | `python3 -m unittest discover -s client -p 'test*.py'` | repo 根跑；122 項；不需遊戲 |
| build DLL | `export VCPKG_ROOT="$HOME/dev/vcpkg" && cmake --fresh --preset build-release-clang-cl-linux && cmake --build build/release-clang-cl-linux` | 產出 `build/release-clang-cl-linux/AgentBridge.dll`；`--fresh` 不可省 |
| 自動部署（選用） | configure 前設 `SKYRIM_MODS_FOLDER`（MO2 `mods/`）或 `SKYRIM_FOLDER` | post-build 把 DLL 放進 `SKSE/Plugins/`；就地覆寫執行中的 DLL 會讓遊戲無聲暴斃，見母 repo dev-env 的部署段 |
| 遊戲內存活檢查 | `curl -s 127.0.0.1:5099/ping` | 需遊戲在跑；遊戲是獨佔資源，走 agentctl 的 game lock |

驗證與測試指令不列這裡——連同「誰跑」一起在 [testing](testing.md)。

## 跨機 / 離線差異

| 環境 | 能跑 | 跑不了的 → 怎麼辦 |
|------|------|------------------|
| 本機（Linux，Manjaro） | client 離線測試全部；DLL 交叉編譯（toolchain 已備） | 遊戲內行為要遊戲在跑，走 agentctl game lock 或記 WAIT_USER |


## 外部工具與 env var

| 名稱 | 用途 | 怎麼取得 / 設定 |
|------|------|----------------|
| `clang-cl`／`cmake`／`ninja` | 交叉編譯 DLL | 系統套件（本機 `/usr/bin`） |
| xwin | Windows CRT／SDK splat | `xwin --accept-license splat --output ~/.xwin-cache`（本機已有 `~/.xwin-cache`） |
| `VCPKG_ROOT` | vcpkg 根目錄 | 本機 `~/dev/vcpkg` |
| `SKYRIM_MODS_FOLDER`／`SKYRIM_FOLDER` | 選用 auto-deploy 目標 | configure 前 export；未設則不部署 |

需要帳號、付費、授權才能取得的：守鐵律 2（授權來源），並在 [WAIT_USER](../../../../WAIT_USER.md) 記一行。

## 交接

- 環境就緒要開工 → [feature-dev](feature-dev/README.md)；先確認驗證跑得動 → [testing](testing.md)。
- 同一個裝機坑第二次撞到 → [common/gotchas](common/gotchas.md)。
