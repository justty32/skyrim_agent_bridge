# Verified end-to-end

[手冊入口](README.md)；下列 CLI 範例以 `client/` 為工作目錄。

Full install → launch → assert → uninstall cycle against the live 109-mod load order
on 2026-08-02, no GUI at any point:

1. `install ModForge/out/ModForgeNavmeshNoop.esp --name QaNoop`
2. `launch` → bridge answered `/ping` 30s later
3. `GET /state?include=plugins` → `{"name": "ModForgeNavmeshNoop.esp", "index": 26}`
4. `POST /console {"cmd": "load <baseline>"}` → `/state` reports `WhiterunExterior15`
5. `kill --mo2`, `uninstall QaNoop`
6. profile semantics match the pre-install state: same enabled mod set, same mod order,
   same active plugin set, same plugin relative order after known engine churn is
   ignored, and same `archives.txt` entries

Step 6 is the one worth keeping. A QA loop that leaves residue in the profile is a
loop you can only run once.

`qa_runner.py` then wraps that whole sequence in one file:

```bash
./qa_runner.py examples/smoke.qa.json --dry-run   # validate, touch nothing
./qa_runner.py examples/smoke.qa.json             # 31s end to end
```

It found a real bug on its first full run — see "the cell name that wasn't" below.

Current source deliberately tightens the historical run above: `load_baseline` no longer
counts `POST /console` plus a fixed settle delay as success. A qa.json must reference an
external `baseline-manifest-v1` through top-level `baseline.manifest`. The runner verifies
the manifest's exact `.ess`/`.skse` sizes and SHA-256 values and binds that directory to
the selected MO2 profile's local-saves directory before sending `load`, then polls `/state`
until `game.load_epoch` advances and the declared
player/cell/interior/dead/closed-MessageBox fingerprint matches. The manifest must live
below the deployment-set `QA_BASELINE_MANIFEST_ROOT`. See
[QA-SCHEMA.md](../QA-SCHEMA.md#baseline-manifest-and-load-proof) for the contract and the
repository examples; deployment supplies its own trusted-root manifest and profile lane.

The deployment-side lane was accepted on 2026-08-16: an external manifest under the
configured trusted root matched the exact `ModpackKRDev0A` `.ess`/`.skse` pair, `/ping`
reported AgentBridge 0.8.0, `game.load_epoch` advanced from 0 to 1, and the complete
declared state fingerprint matched. The four-step live spec passed in 20.8 seconds.
