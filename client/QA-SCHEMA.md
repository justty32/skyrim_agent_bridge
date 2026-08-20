# qa.json — schema

A test run as one file. `qa_runner.py <file.qa.json>` executes it and reports per-step
pass/fail. The step sequence in [`examples/smoke.qa.json`](examples/smoke.qa.json) is a
worked example; its legacy string baseline must be migrated as described below before
the next live run.

```jsonc
{
  "name": "sofia-act1-smoke",
  "description": "free text",
  "baseline": {
    "manifest": "/absolute/deployment/path/baseline-manifest.json"
  },
  "defaults": {
    "settle_seconds": 8,          // pause after console and semantic actions
    "assert_retry_seconds": 20,   // how long assert_state keeps retrying
    "baseline_retry_seconds": 60  // how long load_baseline polls its fingerprint
  },
  "steps":    [ /* run in order; a failure stops the rest */ ],
  "teardown": [ /* ALWAYS runs, even after a failure */ ]
}
```

Put `install` in `steps` and its matching `uninstall` in `teardown`. A run that dies at
step 3 and leaves a test mod in the profile poisons every run after it, so teardown is
not optional and not skippable.

Any step accepts `label` (what shows in the report), `comment` (ignored, for humans) and
`continue_on_fail`.

## Step types

| type | fields | notes |
|---|---|---|
| `install` | `source`, `mod_name`, `enable`, `version`, `comment` | `source` is a mod folder, a folder containing `Data/`, or a bare `.esp`; **relative to the qa.json**, not the shell's cwd |
| `uninstall` | `mod_name`, `keep_files` | |
| `enable` / `disable` | `mod_name` | |
| `launch` | `wait`, `shortcut`, `background_active` | starts SKSE through MO2; waits for the bridge **and** the game thread; `background_active` defaults true and is restored by `kill` |
| `kill` | `mo2`, `timeout` | `mo2: true` also closes MO2, which is what makes the profile writable |
| `load_baseline` | `save`, `retry_for`, `retry_interval`, `timeout`, `state_timeout` | preflights top-level `baseline.manifest`, loads its stem, then polls its state fingerprint; optional `save` must equal the manifest stem |
| `console` | `cmd`, `ref`, `settle`, `timeout` | `ref` is the console's selected reference, for dotted commands |
| `move_to_actor` | `name` or `form_id`, `scope`, `distance`, `retry_for`, `retry_interval`, `settle`, `timeout` | move beside an actor; `scope` is `cell` (default) or `loaded`, distance defaults to 128; may cross cells |
| `activate_actor` | `name` or `form_id`, `scope`, `retry_for`, `retry_interval`, `settle`, `timeout` | start normal dialogue after the actor is loaded in the current cell |
| `select_dialogue` | `text` or `index` or `info_form_id`, `contains`, `retry_for`, `retry_interval`, `settle`, `timeout` | select one visible option; exact text by default |
| `close_dialogue` | `settle`, `timeout` | end active player dialogue |
| `select_message_box` | `text` or `index`, `message`, `retry_for`, `retry_interval`, `settle`, `timeout` | select one modal button; `message` is an optional exact guard |
| `assert_global` | `editor_id`, `expect`, `retry_for`, `retry_interval`, `timeout` | compare a TESGlobal's structured runtime value |
| `wait` | `seconds` | |
| `assert_state` | `expect`, `include`, `radius`, `limit`, `retry_for`, `retry_interval` | see below |
| `handoff_user` | `message`, `expect` | stop and ask a human |

`install` defaults to `force: true` — a QA run should not fail because the previous run
left a folder behind.

## Baseline manifest and load proof

`load_baseline` is fail-closed. Immediately before it sends the console command, it reads
the deployment-owned external manifest, requires exactly one `.ess` and one `.skse`, and
recomputes both sizes and SHA-256 digests. It does not copy, overwrite, or otherwise write
either save member. A missing/mutated member stops the step before `load` is sent.

The deployment must set `QA_BASELINE_MANIFEST_ROOT` to an absolute trusted directory. The
resolved `baseline.manifest` path must be below that root; being absolute by itself is not
enough, because a mutable manifest beside a qa.json would merely attest to itself.

Before sending the command, the runner records `/state`'s process-local `game.load_epoch`.
The SKSE plugin increments that value only when it receives a successful `kPostLoadGame`.
After the console accepts `load <pair.stem>`, the step polls structured `/state` and passes
only when the epoch is strictly newer and every exact `state_signature` field matches at
once. Thus an already-matching cell cannot make a no-op/failed asynchronous command look
successful. Console output and a fixed sleep are not load proof. In particular,
`game.message_box.open` must converge to `false`; the runner does not click or dismiss a
modal on the baseline's behalf.

The external JSON uses the deployment-side `baseline-manifest-v1` pair contract plus a
runtime fingerprint:

```json
{
  "format": "baseline-manifest-v1",
  "profile": "Modpack-KR",
  "isolation": {
    "settings_ini": {
      "LocalSaves": "true",
      "LocalSettings": "true"
    },
    "skyrimcustom_ini": {
      "sLocalSavePath": "__MO_Saves\\"
    }
  },
  "pair": {
    "stem": "ModpackKRDev0A",
    "directory": "/absolute/path/to/profile/saves",
    "members": [
      {
        "extension": ".ess",
        "bytes": 2939894,
        "sha256": "<64 lowercase or uppercase hex digits>"
      },
      {
        "extension": ".skse",
        "bytes": 6163,
        "sha256": "<64 lowercase or uppercase hex digits>"
      }
    ]
  },
  "state_signature": {
    "player.name": "Prisoner",
    "player.cell_form_id": "0x0001605E",
    "player.interior": true,
    "player.flags.dead": false,
    "game.message_box.open": false
  }
}
```

Both `baseline.manifest` and `pair.directory` must be absolute. `profile` must equal the
MO2 profile selected when the load step runs. The manifest and the live profile must both
declare `LocalSaves=true`, `LocalSettings=true`, and
`skyrimcustom.ini [General] sLocalSavePath=__MO_Saves\`; `pair.directory` must then resolve
to the profile's actual `profiles/<profile>/saves` directory. This deliberately supports
only the provable local-saves lane and binds the pair that was hashed to the same-stem pair
Skyrim will load. `members` is a closed set: exactly `.ess` and `.skse`, with no duplicate
member.

The five shown fingerprint paths are mandatory; additional exact dotted-path values are
allowed, but wildcard (`[*]`) paths are rejected because a baseline identity field must
resolve to one exact value. `player.cell_form_id` accepts a JSON integer, a decimal string, or a `0x` hex
string and is normalised against the runtime's numeric FormID. Other fields use exact JSON
equality; booleans also require a JSON boolean, so numeric `0`/`1` cannot impersonate
`false`/`true`.

`qa_runner.py --dry-run` reads and verifies the external manifest and both files so a bad
baseline aborts before an expensive game launch. The load step repeats the same preflight
immediately before the command; changing a member between dry-run and execution is still
caught.

### Migration from the old string form

The old top-level form, `"baseline": "SaveStem"`, is still recognised only so the runner
can emit an actionable error. It can never execute an unverified load, and there is no
`allow-unverified` bypass. Move the stem, absolute directory, pair sizes/hashes, and state
identity into the external manifest, then replace the string with `baseline.manifest`.
Set `QA_BASELINE_MANIFEST_ROOT` to the audited manifest directory, and deploy the matching
AgentBridge DLL that exposes `game.load_epoch`.

Repository-local migration list as of 2026-08-15:

- `examples/smoke.qa.json`
- `examples/bend-time-rings.qa.json`

Their step sequences remain useful examples, but they must point to a deployment-owned
manifest before `--dry-run` or a live run will pass. External/ad-hoc qa.json files using a
bare string need the same one-time migration.

The historical non-local-saves QA profile is intentionally not auto-guessed: it has no
independently provable mapping from a Linux manifest path to the save selected inside
Proton. Migrate the baseline into an isolated local-saves profile (including the exact
`skyrimcustom.ini` path above) before using this P0 load-proof lane.

## assert_state

`expect` maps a dotted path into the `/state` JSON to a condition.

```jsonc
"expect": {
  "player.cell_form_id": { "eq": 90206 },
  "player.actor_values.health.current": { "gte": 100 },
  "player.interior": true,                      // bare value means eq
  "plugins[*].name": { "eq": "MyMod.esp" },
  "nearby_actors[*]": { "count_gte": 3 }
}
```

Paths use `.` for object keys, `[N]` for one array element (negatives allowed) and `[*]`
for all of them. Ask for the optional blocks you reference via `include`
(`nearby_actors`, `cell_actors`, `loaded_actors`, `inventory`, `quests`, `plugins`) — `player` and `game`
are always there. `game.dialogue` always reports whether the menu is open, its speaker,
and structured visible options. `game.message_box` always reports `open`, `ready`, its
message, and buttons in display order.

The semantic interaction steps compose without any screen or desktop input:

```jsonc
{ "type": "assert_state", "include": ["cell_actors"],
  "expect": { "cell_actors[*].name": { "eq": "Falas Indaryn" } } },
{ "type": "move_to_actor", "name": "Falas Indaryn", "distance": 128 },
{ "type": "activate_actor", "name": "Falas Indaryn" },
{ "type": "assert_state",
  "expect": { "game.dialogue.options[*].text": { "eq": "Lower your weapon. Let's talk." } } },
{ "type": "select_dialogue", "text": "Lower your weapon. Let's talk." },
{ "type": "assert_global", "editor_id": "MFLiving_MFLN_Falas_Favor",
  "expect": { "eq": 5 } }
```

For transitions where the actor is not yet in the current cell, use the runtime FormID
reported by an actor block and let the action retry instead of inserting a fixed sleep:

```jsonc
{ "type": "move_to_actor", "form_id": "0x02001234", "scope": "loaded",
  "retry_for": 20, "retry_interval": 1 },
{ "type": "activate_actor", "form_id": "0x02001234", "retry_for": 10 },
{ "type": "select_dialogue", "info_form_id": "0x02005678", "retry_for": 10 }
```

A guarded modal step retries until that exact message and button coexist, then performs
one native menu selection:

```jsonc
{ "type": "select_message_box", "message": "Done Writing", "text": "OK",
  "retry_for": 20, "retry_interval": 0.5 }
```

Bare numeric FormIDs are accepted, but hex strings are easier to compare with console and
plugin tooling. These are **runtime reference IDs**, including the load-order prefix—not
houseCARL's `XXXXXX:Plugin.esp` serialization.

Operators: `eq` `ne` `gt` `gte` `lt` `lte` `contains` `not_contains` `matches` (regex)
`exists` `count_eq` `count_gte` `count_lte`. Exactly one per path.

**`[*]` semantics.** Positive operators pass when **any** element satisfies them; the
negative ones (`ne`, `not_contains`) require **all** of them to. That is how the English
reads: `plugins[*].name not_contains "Foo"` means no plugin matches, not "some plugin
doesn't". A path that resolves to nothing fails every operator except `exists: false`
and `count_*`.

**Assertions retry.** Almost everything the game does in response to a console command is
asynchronous — `coc` returns before the cell finishes loading, an actor value takes a
frame — so `assert_state` re-checks until `retry_for` seconds elapse. It reports the last
attempt's actual values. Set `retry_for: 0` when you specifically mean "right now".
`load_baseline` performs its own mandatory fingerprint polling; a following `assert_state`
is only for scenario-specific conditions beyond baseline identity.

## Three things to assert on, and one not to

**Never assert on console output.** `POST /console` returns at most the console's last
line, and in a real load order other plugins write to it constantly. `output_captured:
true` does not mean the line came from your command. The field is a diagnostic. This is
why every step type above that changes the world is followed by an `assert_state` rather
than a check on its own return value.

**Prefer `cell_form_id` over `cell`.** EditorID strings come from whichever plugin wins
the record, and a plugin that overrides a record without carrying its `EDID` subrecord
forward erases the name at runtime while leaving everything else correct. This is not
hypothetical — `ModForgeNavmeshNoop.esp` overrides `CELL 0x0001605E` (the Bannered Mare)
with no EDID, and with it installed `/state` reports `cell: ""`, `cell_form_id: 90206`,
`interior: true`. The smoke test spent two red runs on that before the cause was found.
FormIDs are engine identity and no plugin can blank them.

**`plugins[*].name` is how you prove an install worked.** `plugins.txt` records what was
requested; `/state?include=plugins` reports what the engine resolved, after MO2's VFS,
missing masters and .esl slotting have had their say.

## handoff_user, and what it does not do

Per plan decision D6 the runner never tries to judge anything visual. It stops and says
what to look at.

- **Terminal (stdin is a tty):** prints the message and blocks. Enter = fine; typing
  anything = that text becomes the failure reason.
- **Not a terminal** (an agent, CI): records the message, marks the step `handoff`, and
  keeps going. Whoever invoked the runner relays it.

A run with handoffs and no failures ends `needs_human`.

## Exit codes

`0` all passed · `1` something failed · `2` passed but a human needs to look ·
`3` the qa.json is invalid

Validation is eager — `--dry-run` checks step types, required fields, operator names,
path syntax and that every `install` source exists, without touching MO2 or the game.
Worth running first: the expensive part of a real run is a game launch, and discovering
a typo at step 12 wastes all of it.
