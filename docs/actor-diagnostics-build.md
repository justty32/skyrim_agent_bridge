# NPC diagnostics v1：隔離候選 DLL

[契約](actor-diagnostics.md)｜[建置入口](runtime-and-build.md)

2026-11-02 已完成 producer／route／capability 與隔離 build，**未部署，live QA 尚未執行**。
使用者遊玩期間沒有存取 runtime HTTP、遊戲鎖、GUI、profile、live DLL 或 DB。

## 可重現驗證

沿用既有 clang-cl／lld-link／xwin／vcpkg installed，未安裝依賴或修改全域設定。
`--fresh` 與獨立 `-B` 防止沿用舊 CMake cache；禁用 manifest install 並清掉 auto-deploy env：

```bash
nice -n 19 env -u SKYRIM_FOLDER -u SKYRIM_MODS_FOLDER \
  VCPKG_ROOT="$HOME/dev/vcpkg" cmake --fresh \
  --preset build-release-clang-cl-linux -B build/actor-diagnostics-20261102 \
  -DVCPKG_MANIFEST_INSTALL=OFF \
  -DVCPKG_INSTALLED_DIR="$PWD/build/release-clang-cl-linux/vcpkg_installed"
nice -n 19 cmake --build build/actor-diagnostics-20261102 --parallel 2
python3 -m unittest discover -s client -p 'test*.py'
python3 -m unittest discover -s tests -v
llvm-readobj --file-headers --coff-exports --coff-imports \
  build/actor-diagnostics-20261102/AgentBridge.dll
sha256sum build/actor-diagnostics-20261102/AgentBridge.dll
```

本次 136 client tests 與 1 producer host test 通過。producer test 直接擷取執行原函式，
不是重寫演算法；host RE doubles 不證明引擎存活性／行為。缺欄位及錯誤讀值有負向斷言，
移除 process guard 的突變已使 host test 失敗（負向對照成立）。
同一 harness 也執行實際 route／FormID parser，涵蓋 schema 拒絕、無效 ID／scope、
timeout 與例外的 503 回覆；不連線 localhost。獨立唯讀 review 無 blocking；
selected spells 的 process guard 是保守策略，詳見契約。

DLL 是 PE32+ AMD64（`Machine=0x8664`、`Magic=0x20B`）；exports 為
`SKSEPlugin_Load`、`SKSEPlugin_Query`、`SKSEPlugin_Version`。Imports 僅系統 DLL：
`WS2_32.dll`、`KERNEL32.dll`、`ole32.dll`、`VERSION.dll`、`USER32.dll`、`SHELL32.dll`。
SHA-256：`aff80463c3de2da61317d4e4416019567e34bdf95b31ece18ea7b72ec3278b3d`。

本機 ignored `build/actor-diagnostics-20261102/` 保存 `build.log`、`configure.log`、
`client-tests.log`、`producer-tests.log`、`pe.txt`、`provenance.json`、`source.patch` 與
`AgentBridge-actor-diagnostics-v1.zip`。來源版本由 `provenance.json` 的 git commit、
source hashes 與 patch 記錄；候選包只含 `SKSE/Plugins/AgentBridge.dll` 和 `SOURCE.json`。
這不是 Windows CI 或遊戲內驗收證明，不能因 build 成功直接宣稱 live 支援。

## 停機部署與 rollback（本次未執行）

只有獲授權並確認 Skyrim、SKSE、MO2 已完全停止後，才由部署線操作。先依部署線流程
依序先取得 desktop lock、再取得 game lock；不要在遊玩中替換載入的 DLL。不要執行預設 build 的 auto-deploy。
設定 `candidate` 為上述候選 DLL、`target` 為部署線核對過的實際 plugin 絕對路徑、
`backup_dir` 為該線持久備份目錄（不使用 `/tmp`），然後：

```bash
# 只供停機窗口人工執行；三個變數必須由部署線明確設定。
: "${candidate:?}" "${target:?}" "${backup_dir:?}"
mkdir -p "$backup_dir"
cp -p -- "$target" "$backup_dir/AgentBridge.dll.before-diagnostics"
sha256sum "$target" "$backup_dir/AgentBridge.dll.before-diagnostics"
cp -- "$candidate" "$target.diagnostics-new"
sha256sum "$candidate" "$target.diagnostics-new"
mv -- "$target.diagnostics-new" "$target"
```

備份檔名須在該次唯一備份目錄中，禁止覆寫先前 rollback 來源。啟動後驗能力與契約，
只做授權的單一 NPC read QA，保存 snapshot。任何 crash、錯誤 identity、錯誤 signature、
空值與不可用狀態混淆，或新能力宣告但 route 失敗，皆停下並在再次完全停機後回退：

```bash
cp -p -- "$backup_dir/AgentBridge.dll.before-diagnostics" "$target.rollback-new"
sha256sum "$backup_dir/AgentBridge.dll.before-diagnostics" "$target.rollback-new"
mv -- "$target.rollback-new" "$target"
```

回退舊 DLL 後 client 應回 `unsupported`，不送 diagnostics route；無需改 ESP／存檔／INI。
部署線仍須記錄實際 target、前後 hash 與 QA 結果；此文沒有授權現在部署。
