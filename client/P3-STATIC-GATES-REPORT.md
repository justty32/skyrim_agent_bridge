# P3 Static Gates Report

Date: 2026-08-06

## Summary

Implemented `mo2ctl static-gates`, an offline houseCARL-backed gate runner for the
third-party mod pipeline. It does not launch Skyrim and it does not edit the MO2
profile.

The command starts the local houseCARL stdio MCP server directly, using explicit paths
for this Linux/Proton MO2 install instead of `set_mo2_instance`.

## Interface

```bash
./mo2ctl.py static-gates --write-baseline /tmp/before.json --json
./mo2ctl.py static-gates \
  --mod "New Mod" \
  --plugin NewMod.esp \
  --baseline /tmp/before.json \
  --report /tmp/newmod-static.json \
  --json
```

Useful optional scopes:

- `--dialogue-formid <FORMID>` for `housecarl_validate_dialogue`
- `--asset <Data-relative path>` for `housecarl_asset_status`
- `--mesh <Data-relative mesh path>` for `housecarl_nif_inspect`
- `--crash-since <ISO timestamp>` to triage new `crash-*.log` files without assuming
  every Proton log has a call stack

## Gates

The runner captures:

- `housecarl_load_order_status`
- `housecarl_check_errors`
- `housecarl_skse_inventory`
- `housecarl_validate_scripts`
- optional dialogue, asset, and NIF checks

`loadorder.txt` churn from these game-written CC entries is explicitly ignored:

- `ccbgssse068-bloodfall.esl`
- `ccbgssse069-contest.esl`
- `ccvsvsse004-beafarmer.esl`

Existing whole-order findings are not treated as a new mod failure without a baseline.
For the real pipeline, take a baseline before installing a mod and compare the after
capture against it. New missing masters, new SKSE contested/version-locked diagnostics,
and scoped plugin validator failures are red.

## Crash Triage

The crash triage parser:

- reads the configured `crash_logs` folder from `housecarl_load_order_status`
- deduplicates crash logs whose timestamps are within one second
- marks Proton logs with no `CALL STACK` as `unable_to_attribute` instead of pretending
  a culprit is known
- ignores Wine's bogus huge process-memory value by not using memory size as a signal
- bins uptime as load/plugin-conflict, entry/initialization, or content/playtime

## Verification

Commands run:

```bash
python3 -m py_compile client/mo2ctl.py client/test_mo2ctl.py client/qa_runner.py
python3 -m unittest discover -s client -p 'test*.py'
client/mo2ctl.py --json static-gates --limit 20 --max-chars 12000
client/mo2ctl.py --json static-gates --plugin _ResourcePack.esl --limit 5 --max-chars 8000 --write-baseline /tmp/mo2ctl-static-before.json
client/mo2ctl.py --json static-gates --plugin _ResourcePack.esl --limit 5 --max-chars 8000 --baseline /tmp/mo2ctl-static-before.json
```

The live smoke reached houseCARL through the new direct MCP client and returned a
structured report for the active `Default` profile. It did not launch the game or modify
the profile. The scoped `_ResourcePack.esl` run has existing script findings when judged
alone, then passes when compared to its own before-capture baseline, proving that the gate
uses before/after semantics rather than byte or absolute-zero checks.
