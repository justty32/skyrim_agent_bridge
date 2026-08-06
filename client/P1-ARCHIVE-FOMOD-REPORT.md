# P1 Archive + FOMOD Report

Date: 2026-08-06

## Files Changed

- `client/mo2ctl.py`
- `client/README.md`
- `client/test_mo2ctl.py`
- `client/P1-ARCHIVE-FOMOD-REPORT.md`

## New Interfaces

- `mo2ctl inspect <archive-or-dir> [--write-choices PATH]`
  - Reads archive/folder structure without installing.
  - Detects `fomod/ModuleConfig.xml`.
  - Emits a summary and can write replayable `mo2ctl-fomod-choices-v1` JSON.

- `mo2ctl install <archive-or-dir-or-esp> [--fomod-choices PATH] [--priority SPEC]`
  - Supports `.zip` with stdlib `zipfile` and zip-slip path checks.
  - Detects `.7z` / `.rar` and uses `7z` or `unar` when present; otherwise returns `handoff_user`.
  - Accepts `--priority bottom`, `--priority top`, `--priority before:<mod>`, and `--priority after:<mod>`.
  - Defaults to `--priority bottom`; it no longer inserts unverified mods at top file priority.
  - Writes `mo2ctl-fomod-choices.json` into installed FOMOD mods.

## FOMOD Supported Subset

Supported:

- `fomod/info.xml` name/version extraction.
- `requiredInstallFiles`.
- `installSteps` / `group` / `plugin`.
- Static plugin `type` values such as `Required`, `Recommended`, and `Optional`.
- File and folder installs using `source` plus `destination` or `target`.
- Deterministic default selection for required/recommended options where group cardinality allows it.

Falls back to `handoff_user`:

- `conditionalFileInstalls`.
- Step `visible` conditions.
- `dependencyType` and other runtime dependency evaluation.
- Flag-driven choices and flag propagation.
- Ambiguous `SelectExactlyOne` or `SelectAtMostOne` groups without one deterministic required/recommended pick.
- `SelectAtLeastOne` groups with no required/recommended default pick.
- Missing FOMOD source paths referenced by ModuleConfig.
- `.7z` / `.rar` when neither `7z` nor `unar` is available.

## BSA Handling

When an enabled mod contributes `.bsa` or `.ba2` files whose basename does not match one
of that mod's plugins, `install` appends those archive names to the active profile's
`archives.txt`. `uninstall` removes the same unmanaged archive entries.

## Verification

- `python3 -m py_compile client/mo2ctl.py client/test_mo2ctl.py`
- `python3 -m unittest discover -s client -p 'test*.py'` (9 tests)
- Real FOMOD inspect: `NPC AI Process Position Fix - NG-69326-1-1-1-1665790814.zip`
  returns `handoff_user` for `Files/Plugin: no deterministic default for SelectAtLeastOne`.
- Real QA install/uninstall smoke test: `Proper Aiming-1884-1-0.zip`
  installed as `P1 QA Proper Aiming Smoke` on the `QA` profile with default
  `bottom` priority, activated `Proper Aiming.esp`, made no `archives.txt`
  changes, touched only `QA/loadorder.txt`, `QA/modlist.txt`, and
  `QA/plugins.txt`, then uninstalled cleanly and returned `profiles` to a clean
  worktree. `ModOrganizer.ini` was restored to `selected_profile=@ByteArray(Default)`
  with CRLF preserved.

Both checks pass.
