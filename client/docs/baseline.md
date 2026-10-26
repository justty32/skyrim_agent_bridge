# Verified end-to-end

[手冊入口](README.md)；下列 CLI 範例以 `client/` 為工作目錄。

QA 循環必須在 uninstall 後恢復 profile semantics：enabled mod set、mod order、
active plugin set、忽略已知 engine churn 後的 plugin relative order，以及 `archives.txt` entries。

`qa_runner.py` 執行整個流程：

```bash
./qa_runner.py examples/smoke.qa.json --dry-run
./qa_runner.py examples/smoke.qa.json
```

`load_baseline` 必須證明載入成功；`POST /console` 加固定 settle delay 不算成功。
A qa.json must reference an
external `baseline-manifest-v1` through top-level `baseline.manifest`. The runner verifies
the manifest's exact `.ess`/`.skse` sizes and SHA-256 values and binds that directory to
the selected MO2 profile's local-saves directory before sending `load`, then polls `/state`
until `game.load_epoch` advances and the declared
player/cell/interior/dead/closed-MessageBox fingerprint matches. The manifest must live
below the deployment-set `QA_BASELINE_MANIFEST_ROOT`. See
[QA-SCHEMA.md](../QA-SCHEMA.md#baseline-manifest-and-load-proof) for the contract and the
repository examples; deployment supplies its own trusted-root manifest and profile lane.
