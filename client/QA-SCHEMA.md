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
