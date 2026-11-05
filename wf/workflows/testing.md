# testing — 跑測試 / 驗證

[WORKFLOWS](../WORKFLOWS.md)｜[INDEX](../INDEX.md)

改完怎麼確認沒壞：有哪些驗證、各自的指令、哪些 agent 自己跑得了、哪些得交給使用者。

**何時用**：改完程式要驗、使用者說「跑測試」、重構前要記基準。
**何時不用**：環境還沒裝好、不知道指令哪來 → [dev-env](dev-env.md)；驗證紅了要查成因 → [investigation](investigation.md)。

## Done when

- 下表標「改完必跑」的列指令回傳 0。
- 「誰跑」是使用者的列，已在 [WAIT_USER](../../../../WAIT_USER.md) 各留一行（寫明跑什麼、什麼算過）。

## 驗證表

| 驗證 | 指令 | 誰跑 |
|------|------|------|
| 快速／完整驗證（改完必跑） | `python3 -m unittest discover -s client -p 'test*.py'` | agent |
| DLL build | `cmake --fresh --preset build-release-clang-cl-linux && cmake --build build/release-clang-cl-linux`（先 `export VCPKG_ROOT`） | agent（改 `src/` 或 CMake 時；toolchain 在本機，需要時才跑） |
| 遊戲內行為（`/state`、dialogue、MessageBox） | 遊戲在跑時 `qa_runner` 跑 `qa.json`，或 `curl 127.0.0.1:5099/ping` | 使用者 → [WAIT_USER](../../../../WAIT_USER.md)（或持 game lock 的線） |

「誰跑」只有兩種值：**agent**（本機跑得動）、**使用者 → WAIT_USER**（要實機、外部服務、帳號、付費、目視）。判不準就當後者。

## 測試分類

- `fast／full`：`client/` 的 unittest；離線、不需遊戲，每次改動都跑。
- `build`：DLL 交叉編譯；改 `src/`、`CMakeLists.txt`、`vcpkg*.json`、preset 後跑。
- `external`：需要遊戲執行中的驗證（runtime、手感、crash）；agent 代跑要拿 game lock，否則記 WAIT_USER。

## 綠燈不等於有檢查

**一道檢查通過，可能是因為它根本沒在檢查。** 這不是假設：曾在一天內抓到四個恆真檢查——結構稽核、「鎖已釋放」的檢查指向已刪目錄、連結檢查器走到子 repo 指標就停、自製字元偵測。

**規則：新增或修改一道檢查時，要證明它能變紅。** 先餵一個**應該被擋**的輸入，確認 exit ≠ 0；再餵正確的輸入，確認 exit = 0。**沒做過這個雙向驗證的綠燈不算證據。**

兩個推論：檢查器的**涵蓋範圍要跟著結構走**——拆出子 repo、搬走目錄之後，回頭確認檢查器還看得到那些地方（見 [refactor/moving-things.md](refactor/moving-things.md)）；**靜態全過不等於畫面上是對的**，方框、亂碼、截斷、手感只有人眼看得出來，這類記到 [WAIT_USER](../../../../WAIT_USER.md)，不要自己宣稱通過。

## 交接

- 綠燈後回 [feature-dev](feature-dev/README.md) 接完剩下的步驟（code map → 文檔 → commit）。
- 同一個紅燈第二次撞到 → [common/gotchas](common/gotchas.md)。

## 截圖候選的固定離線驗證

repo 根執行；輸出放工作區外。三個 host 測試分別覆蓋像素／request service、真正 readback
狀態機及 hook 前鏈。`--corrupt` 是受控錯誤 oracle，預期 exit 1。

```sh
out=/var/tmp/skyrim-agent-bridge-screenshot-2026-11-05
c++ -std=c++20 -pthread -Wall -Wextra -Werror -Isrc src/ScreenshotCore.cpp tests/native/screenshot_core.cpp -o "$out/screenshot-core-test"
"$out/screenshot-core-test"
"$out/screenshot-core-test" --corrupt
c++ -std=c++20 -pthread -Wall -Wextra -Werror -Isrc src/ScreenshotCore.cpp src/ScreenshotReadback.cpp tests/native/screenshot_readback.cpp -o "$out/screenshot-readback-test"
"$out/screenshot-readback-test"
"$out/screenshot-readback-test" --corrupt
c++ -std=c++20 -Wall -Wextra -Werror -Isrc tests/native/screenshot_hook.cpp -o "$out/screenshot-hook-test"
"$out/screenshot-hook-test"
"$out/screenshot-hook-test" --corrupt
c++ -std=c++23 -Wno-deprecated-literal-operator -pthread -DAGENT_BRIDGE_HTTP_HOST_TEST -Isrc -Ibuild/release-clang-cl-linux/vcpkg_installed/x64-windows-skse-clang/include src/HttpServer.cpp tests/http/loopback.cpp -o "$out/http-host"
"$out/http-host"
"$out/http-host" --mutated-byte-expectation
python3 -B -m unittest discover -s client -p 'test*.py'
```

HTTP 測試使用 production transport 的 Linux socket seam；Windows branch 由 DLL cross-build
驗編譯，Winsock／DXVK／遊戲畫面仍需實機驗證。client 的
`test_screenshot.py` 使用真正 loopback server，覆蓋短讀、超量、MIME／BMP／JSON錯誤與總deadline。

截圖隔離 build 必先 unset `SKYRIM_FOLDER`、`SKYRIM_MODS_FOLDER`，保留舊本機候選，使用
既有 toolchain／installed dependencies；不可在驗證時下載或安裝依賴。例如：

```sh
env -u SKYRIM_FOLDER -u SKYRIM_MODS_FOLDER VCPKG_ROOT="$HOME/dev/vcpkg" cmake --fresh --preset build-release-clang-cl-linux -B "$out/build" -DVCPKG_MANIFEST_INSTALL=OFF -DVCPKG_INSTALLED_DIR="$PWD/build/release-clang-cl-linux/vcpkg_installed"
env -u SKYRIM_FOLDER -u SKYRIM_MODS_FOLDER cmake --build "$out/build" -j 2
```

實機五項行為與未驗界線見 [截圖候選](../../docs/screenshot.md)。

## 原生 keyboard tap 候選離線驗證

0.11.0未部署；API／primary／runtime邊界見[契約](../../docs/input.md)。
repo根執行；沿用上節的既有installed dependencies與隔離build命令，禁止auto-deploy。

```sh
out=/var/tmp/skyrim-agent-bridge-input-2026-11-05
c++ -std=c++20 -pthread -Wall -Wextra -Werror -Isrc src/InputCore.cpp tests/native/input_core.cpp -o "$out/input-core-test"
"$out/input-core-test"
"$out/input-core-test" --mutate
c++ -std=c++23 -Wno-deprecated-literal-operator -pthread -DAGENT_BRIDGE_HTTP_HOST_TEST -Isrc -Ibuild/release-clang-cl-linux/vcpkg_installed/x64-windows-skse-clang/include src/HttpServer.cpp src/InputRoute.cpp src/InputCore.cpp tests/http/input_loopback.cpp -o "$out/input-http-host"
"$out/input-http-host"
"$out/input-http-host" --mutate
python3 -B -m unittest discover -s client -p 'test*.py'
```

兩個 `--mutate` 都應exit1；native host執行production queue/state machine與cache seam，
HTTP host經真loopback到production transport/handler/core。host環境與keyboard poll是可控替身，
不能取代真engine mapping、vtable phase、menu/focus或遊戲效果驗證；production Ping由DLL build
與接線檢查承接，HTTP host的Ping只用於transport smoke。既有三個截圖host與HTTP回歸仍須綠。
