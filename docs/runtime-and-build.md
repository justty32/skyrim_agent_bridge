# 行程內設計、限制與建置

[文件入口](README.md)

## Design notes

**Port 5099，只聽 loopback。** 必須使用 `INADDR_LOOPBACK`，不得使用 `INADDR_ANY`；此服務可
執行 console command，不能讓網路存取。Linux client 也 hardcode 5099，改 port 必須兩端同步。

**兩個 thread，一個 seam。** Accept loop 使用獨立 thread；幾乎所有 `RE::` read 只能在 game
main thread 安全執行。需要 game state 的 route 將 callable 交給 `GameThread::Run`，透過 SKSE
task interface marshal，預設 timeout 為 3 秒。Load screen 期間 task queue 可能完全不 drain；
timeout 回 503，由 runner retry，避免 handler 卡住 socket thread。

**Hand-rolled HTTP，不使用 cpp-httplib。** 介面只有少量 localhost JSON route 與單一 client；
新增 dependency 都必須通過 clang-cl + lld-link + xwin cross-compile。服務一次處理一個
connection，使用 `Connection: close`，request 上限 1 MiB。

**沒有 clean shutdown path。** SKSE 沒有 unload message，thread 活到 process 結束；
`Http::Stop()` 目前未使用。

## Pitfall: do not hook `ConsoleLog::VPrint`

**禁止 hook `ConsoleLog::VPrint`。** 在有其他 console plugin 的 load order 中，五-byte
prologue detour 會互相覆寫；曾於 Papyrus VM init 約 6.6 秒時 crash：

```
Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x000158B3D6AE
Access Violation: Tried to execute memory at 0x000158B3D6AE
[ 0][P] 0x000158B3D6AE
[ 1][S] 0x6FFFEA014404   AgentBridge.dll+0054404
[ 2][S] 0x6FFFE9819F94   ConsoleUtilSSE.dll+00B9F94
```

`write_branch<5>` 只保存被覆寫的 5 bytes；當 `MoreInformativeConsole.dll`、
`ConsoleUtilSSE.dll` 等 plugin 也 patch 同一位置時，保存的「原始」內容可能已是別人的半段
`jmp`。若需改善 output capture，依序選擇：讀更多 `ConsoleLog` state、使用已持有 hook 且有
API 的 plugin、hook 無人競爭的 call site；不得競爭 popular engine function 的 prologue。

目前做法是由 `Console::Execute` 寫入 sentinel、執行 command，再讀
`ConsoleLog::lastMessage`；若仍是 sentinel 就回空值。限制如下：

- **只保留一行。** `sqs` 與 `help` 只回最後一行。
- **Sentinel 只適用快速 command。** `load`、`coc` 仍可能混入其他 mod 的輸出，例如
  `GetInFaction >> 0.00` 或 `GetNumericPackageData >> 360.00`。

因此必須 **assert `/state`，不得 assert console output**。Output 只供診斷；
`output_captured: true` 不代表該行來自你的 command。

## Pitfall: `winsock2.h` goes *after* CommonLib, never before

一般 Windows 慣例是先 include `winsock2.h`，但本專案相反。CommonLibSSE-NG 自帶 Win32
re-declarations (`REX::W32`)，`REX/W32/BASE.h` 遇到真正 Windows header 會 hard-error：

```
error: Windows API detected. Please move any Windows API includes after CommonLib, or remove them.
```

`minwindef.h` 的 `#define MAX_PATH 260` 也會破壞後續
`inline constexpr auto MAX_PATH{260u}`。因此 `src/PCH.h` 必須先放 `RE/Skyrim.h`，再放 socket
headers；macro 只影響後續 parsing，而 `REX::W32` 名稱有 namespace，此順序安全。

## Build

Linux host 以 `clang-cl` cross-compile 成 Windows DLL。需要將 `xwin` splat 到
`~/.xwin-cache` 並設定 `VCPKG_ROOT`：

```bash
export VCPKG_ROOT="$HOME/dev/vcpkg" && cmake --fresh --preset build-release-clang-cl-linux && cmake --build build/release-clang-cl-linux
```

`--fresh` 不可省略：preset 以裸名 `clang-cl` 指定編譯器，第二次 configure 時 cache 中的
絕對路徑與裸名不等，CMake 會中途重跑 configure 並失去 xwin toolchain 的 include／libpath，
報 `Check for working CXX compiler: /usr/bin/clang-cl - broken`。

輸出：`build/release-clang-cl-linux/AgentBridge.dll`。

選用 auto-deploy：configure 前設定 `SKYRIM_MODS_FOLDER`（MO2 `mods/`）或
`SKYRIM_FOLDER`，post-build step 會將 DLL 放入 `SKSE/Plugins/`。

## Verifying it works

遊戲執行時：

```bash
curl -s 127.0.0.1:5099/ping && echo && curl -s 127.0.0.1:5099/state
```

`127.0.0.1` 可跨越 Proton boundary；plain wine 與 Proton 9 + pressure-vessel 均已驗證，
listening socket 屬於 container 內的 `wineserver`。
