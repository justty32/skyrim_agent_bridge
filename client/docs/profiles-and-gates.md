# Profile checkpoint 與 static gates

[手冊入口](README.md)；下列 CLI 範例以 `client/` 為工作目錄。

`install` also updates `profiles/manifest.json` by default. The entry records mod name,
profile, version, source path / URL, archive sha256, FOMOD choices, plugins, and BSA
files. When `mongosh` or `mongo` can reach the mod-library MongoDB, the sha256 is
checked against the `archives` collection where `_id` is the digest; otherwise the
entry records `archive_library: unchecked`. The URI defaults to
`mongodb://127.0.0.1:27017` (matching `mod-library/db/*.py`) and can be overridden
with `SKYRIM_MONGO_URI`.

Every install, uninstall, enable, disable, and completed try now ends at one profile
checkpoint boundary. `profiles/manifest.json` remains the provenance ledger, while
`profiles/profile-state.json` records the live profile hashes, enabled mods, plugin
order, and manifest drift. `reconcile` is read-only by default and reports external
changes since that checkpoint. Use `reconcile --apply --source <tag>` to synchronize
existing manifest entries' enabled flags and write a fresh checkpoint; it never invents
provenance for unregistered mods or removes stale entries. `--fail-on-drift` makes the
dry-run return failure when file hashes or enabled flags have drifted, the checkpoint
is absent, its selected profile differs, or the provenance hash has changed.

`ProfileEdit` stages modlist/plugins/loadorder (and unmanaged archives) in memory;
`commit_profile()` validates the triplet before publishing changed files and the
checkpoint. Original newline conventions are preserved. `profile-state.json` also
contains `mod_order` as `[name, enabled]` pairs, including disabled mods. Provenance
fields such as source URL, installed priority and contributed plugins retain their
installation meaning; legacy `enabled` fields are synchronized for compatibility.
`manifest.updated_at` dates ledger writes, while `checkpoint_at` dates live snapshots.
The skip flags retain their explicit `manifest_dirty` marker in the checkpoint.

Publication uses per-file atomic replacement and rolls back bytes/mtimes on ordinary
write failure. This is not a multi-file crash-atomic transaction: consumers must verify
the checkpoint's three file hashes and `manifest_sha256`, using `reconcile` after a
crash or an external writer. All mutation requires exclusive access with MO2 and the
game stopped; the command's process check does not acquire deployment locks.
`reconcile --apply` changes only the ledger/checkpoint, preserving profile bytes and
mtimes. It never resolves unknown provenance by inventing source entries.

`try-pass` checkpoints before recording/merging the profile. `try-fail` checkpoints
the restored main profile and records generated ledger changes in a local commit if
needed; legacy repos without a checkpoint ignore rule retain it instead of deleting
it. Git restores/merges still use Git to restore tracked bytes. MO2/hand-edited changes
require an explicit reconcile after the writer exits; this tool installs no watcher
or deployment launcher hook.

Offline regression suite (from the repository root):

```bash
python3 -m unittest discover -s client -p 'test*.py'
```

`test_mo2ctl.py` covers commands and temporary Git profiles;
`test_mo2ctl_checkpoint.py` covers staged publication, rollback and reconcile.

Profile git helpers deliberately compare profile state semantically, not byte-for-byte.
The current known churn is: Skyrim may write
`ccbgssse068-bloodfall.esl`, `ccbgssse069-contest.esl`, and
`ccvsvsse004-beafarmer.esl` back into `loadorder.txt`, and MO2 may rewrite the
`plugins.txt` header / file formatting. `profile-semantics` ignores comments and those
three loadorder entries, then compares enabled mod set, mod order, active plugin set,
plugin relative order, and `archives.txt` entries. `try-begin` first absorbs clean
engine churn into a profile repo commit, then opens `try/<mod>`.

`static-gates` runs houseCARL's offline validators through the local stdio MCP server
(`~/tools/housecarl/server/housecarl-mcp` by default) and reports `pass`, `warn`, or
`fail` without launching the game. It captures load-order status, check-errors, SKSE
inventory, and script validation; optional arguments add dialogue FormIDs, asset paths,
and NIF inspection. Use a before/after baseline around a `try/<mod>` branch:

```bash
./mo2ctl.py static-gates --write-baseline /tmp/before.json --json
./mo2ctl.py static-gates --plugin NewMod.esp --baseline /tmp/before.json --report /tmp/newmod-static.json --json
```

For crash triage, CrashLogger's `crash-YYYY-MM-DD-HH-MM-SS.log` timestamp is local wall-clock
time. `--crash-since` accepts ISO time; include an explicit offset when passing an absolute
timestamp (for example, `2026-08-16T03:00:00Z`). An offset-free value is interpreted as local time.

The comparison is deliberately semantic. The three game-written CC loadorder warnings are
ignored, existing whole-order findings are warnings unless they get worse, and SKSE DLL
diagnostics become red only when a baseline comparison shows new contested or
version-locked entries. Scoped `--plugin` validators are stricter because they describe
the mod under test directly.
