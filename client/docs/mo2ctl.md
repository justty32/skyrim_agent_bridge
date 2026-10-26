# mo2ctl

[手冊入口](README.md)；下列 CLI 範例以 `client/` 為工作目錄。

```bash
./mo2ctl.py status [--mod NAME]          # what's running, is the profile safe to edit
./mo2ctl.py inspect <archive-or-dir> [--write-choices choices.json]
./mo2ctl.py install <archive-or-dir-or-esp> [--name NAME] [--priority bottom] [--fomod-choices choices.json] [--no-enable]
./mo2ctl.py uninstall <name> [--keep-files]
./mo2ctl.py profile-status
./mo2ctl.py profile-semantics [--ref HEAD]
./mo2ctl.py profile-absorb-churn
./mo2ctl.py static-gates --plugin NewMod.esp --baseline before.json --report after.json
./mo2ctl.py select-profile modpack-main
./mo2ctl.py try-begin "Mod Name"
./mo2ctl.py try-fail
./mo2ctl.py try-pass -m "Validate Mod Name"
./mo2ctl.py enable|disable <name>
./mo2ctl.py reconcile [--apply] [--source external] [--fail-on-drift]
./mo2ctl.py launch [--shortcut SKSE] [--wait 180] [--no-wait] [--background-active]
./mo2ctl.py kill [--mo2]
```

`--json` on any subcommand (either side of it) for machine-readable output; that's
what the Phase 3 runner will use. Overrides: `MO2_ROOT`, `MO2_PROFILE` (otherwise
read from `ModOrganizer.ini`'s `selected_profile`).

`install` takes a `.zip`, a mod folder, a folder whose only child is `Data/`, or a bare
`.esp` — that last one because `ModForge/out/` is exactly a pile of loose plugins.
`.7z` and `.rar` are routed through `7z` or `unar` when one is available; otherwise the
tool returns `handoff_user` and expects a manually unpacked folder.

New mods now default to `--priority bottom` in `modlist.txt`, so an unverified texture /
mesh / script mod does not silently win every file conflict. Use `--priority top`,
`--priority before:<mod name>`, or `--priority after:<mod name>` when a deliberate
insertion point is known. Plugin order is still appended to `plugins.txt` /
`loadorder.txt`; file priority and plugin order are separate decisions.

`inspect` parses archive structure without installing it. When it sees
`fomod/ModuleConfig.xml`, `--write-choices` writes a replayable
`mo2ctl-fomod-choices-v1` JSON file. `install --fomod-choices choices.json` materializes
that selection and also writes `mo2ctl-fomod-choices.json` inside the installed mod
folder. The supported FOMOD subset is deliberately conservative: plain
`requiredInstallFiles`, install steps, groups, plugins, static `type` values, and file /
folder installs. `conditionalFileInstalls`, step visibility conditions, `dependencyType`,
flag-driven choices, and ambiguous exactly-one groups return `handoff_user` instead of
guessing.

When an enabled mod contributes `.bsa` / `.ba2` files whose basename does not match one
of its enabled plugins, `install` appends those archive names to the active profile's
`archives.txt`; `uninstall` removes the same unmanaged archive entries.
