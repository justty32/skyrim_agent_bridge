# qa.json — schema

A test run as one file. `qa_runner.py <file.qa.json>` executes it and reports per-step
pass/fail. Worked example: [`examples/smoke.qa.json`](examples/smoke.qa.json).

```jsonc
{
  "name": "sofia-act1-smoke",
  "description": "free text",
  "baseline": "<save filename without extension>",   // default for load_baseline
  "defaults": {
    "settle_seconds": 8,          // pause after load_baseline / console
    "assert_retry_seconds": 20    // how long assert_state keeps retrying
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
| `launch` | `wait`, `shortcut` | starts SKSE through MO2; waits for the bridge **and** the game thread |
| `kill` | `mo2`, `timeout` | `mo2: true` also closes MO2, which is what makes the profile writable |
| `load_baseline` | `save`, `settle`, `timeout` | falls back to the top-level `baseline` |
| `console` | `cmd`, `ref`, `settle`, `timeout` | `ref` is the console's selected reference, for dotted commands |
| `wait` | `seconds` | |
| `assert_state` | `expect`, `include`, `radius`, `limit`, `retry_for`, `retry_interval` | see below |
| `handoff_user` | `message`, `expect` | stop and ask a human |

`install` defaults to `force: true` — a QA run should not fail because the previous run
left a folder behind.

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
(`nearby_actors`, `inventory`, `quests`, `plugins`) — `player` and `game` are always there.

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
