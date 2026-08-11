# client/ — the Linux half

`agent-bridge` (the DLL) is the eyes and hands inside the game. This directory is
everything on the Linux side of that socket. Kept in the same subproject on purpose:
the port number, the `/state` field names and the client that reads them are one
contract, and a two-sided edit should be one commit.

| Tool | Status | What it does |
|---|---|---|
| `mo2ctl.py` | ✅ verified end-to-end 2026-08-02 | Drive MO2 without its GUI: install / uninstall / enable / disable / launch / kill / status |
| `bridge.py` | ✅ | Talking to the in-game HTTP bridge. Owns the port; everything else imports it |
| `qa_runner.py` | ✅ 0.6.0 runtime-verified; 0.7.0 MessageBox step offline-tested | Execute a `qa.json`, including retryable actor/dialogue/MessageBox steps, and report pass/fail. Schema: [QA-SCHEMA.md](QA-SCHEMA.md) |
| `qa_mcp.py` | ✅ 0.3.0 runtime-verified; 0.4.0 MessageBox tool offline-tested; client restart required to discover new tool schemas | MCP server: state, console, actor, dialogue, MessageBox, global, structured wait, and whole-run tools |

stdlib only, no venv. This has to keep working while the rest of the toolchain is
mid-rebuild, and a QA harness that needs its own install step before it can test
anything is a harness you stop using.

## mo2ctl

```bash
./mo2ctl.py status [--mod NAME]          # what's running, is the profile safe to edit
./mo2ctl.py inspect <archive-or-dir> [--write-choices choices.json]
./mo2ctl.py install <archive-or-dir-or-esp> [--name NAME] [--priority bottom] [--fomod-choices choices.json] [--no-enable]
./mo2ctl.py uninstall <name> [--keep-files]
./mo2ctl.py profile-status
./mo2ctl.py profile-semantics [--ref HEAD]
./mo2ctl.py profile-absorb-churn
./mo2ctl.py static-gates --plugin NewMod.esp --baseline before.json --report after.json
./mo2ctl.py select-profile QA|Default
./mo2ctl.py try-begin "Mod Name"
./mo2ctl.py try-fail
./mo2ctl.py try-pass -m "Validate Mod Name"
./mo2ctl.py enable|disable <name>
./mo2ctl.py launch [--wait 240]          # SKSE through MO2, waits for the bridge
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

`install` also updates `profiles/manifest.json` by default. The entry records mod name,
profile, version, source path / URL, archive sha256, FOMOD choices, plugins, and BSA
files. When `mongosh` or `mongo` can reach `mongodb://127.0.0.1:27018/skyrim`, the
sha256 is checked against the `archives` collection where `_id` is the digest; otherwise
the entry records `archive_library: unchecked`.

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

The comparison is deliberately semantic. The three game-written CC loadorder warnings are
ignored, existing whole-order findings are warnings unless they get worse, and SKSE DLL
diagnostics become red only when a baseline comparison shows new contested or
version-locked entries. Scoped `--plugin` validators are stricter because they describe
the mod under test directly.

### Verified end-to-end

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

## Three things that are not obvious

**The profile files disagree about line endings.** `modlist.txt` and `loadorder.txt`
are CRLF; `plugins.txt` is LF. Same directory, same program wrote all three. Normalising
them is not harmless: a `sed 's|^+AgentBridge$|...|'` against CRLF content silently
matches nothing, which is how an earlier manual edit appeared to succeed and did not.
So `read_file` carries each file's own ending along and `write_file` puts it back.

**Editing the profile while MO2 is running does not conflict — it silently reverts.**
MO2 holds the profile in memory and writes `modlist.txt` / `plugins.txt` back out on
exit or profile switch. Your edit lands, MO2 knows nothing about it, and minutes later
MO2 quits and overwrites it. No error, no warning, and the failure shows up as "the mod
I installed isn't loaded" long after the cause. Every mutating subcommand therefore
refuses while MO2 *or* the game is up. `--force` exists; the plan only anticipated the
game as a blocker, and MO2 turns out to be the one that actually bites.

**Process detection matches `argv[0]`, not the whole command line.** Substring matching
is wrong in both directions here. `protontricks-launch --appid 489830 .../ModOrganizer.exe
moshortcut://:SKSE` mentions MO2 in its arguments, so a `-f`-style match counted the
launcher, its wrapper and its python parent as three extra copies of MO2 — measured:
five "MO2 processes" when there was one. And the Steam/Proton chain around
`SkyrimSELauncher.exe` outlives the game for the whole session, so anything looser than
an exact filename compare would report the game as permanently running and wedge the
lock shut.

Related: this reads `/proc` directly instead of shelling out. `pkill -f <pattern>` killed
the invoking shell twice during this project, because the pattern matched the shell's own
command line. Scanning `/proc` and skipping our own pid cannot do that.

## launch

```
protontricks-launch --appid 489830 <MO2>/ModOrganizer.exe moshortcut://:SKSE
```

MO2 has to run inside the game's own Proton prefix — usvfs needs MO2 and the game in one
wine session, which is also why there is no separate MO2 wine prefix to point at.
`moshortcut://:SKSE` is MO2's own name for the `customExecutables` entry in
`ModOrganizer.ini`, so this is the same path the GUI's Run button takes. Use `--shortcut`
if that entry is renamed.

`launch` then polls `/ping` rather than guessing at a sleep, and gives up with
`bridge.reachable: false` instead of hanging. Observed cold start on this load order:
16–30s.

**`/ping` answering does not mean the game is ready.** `/ping` is served on the socket
thread and keeps answering right through load screens — deliberately, so a runner can
tell "process alive, game busy" from "process dead". The first `/state` after launch
reliably 503s with `game thread did not respond in time`. That cost the smoke test a
red run before `qa_runner`'s launch step learned to wait for `/state` as well. `mo2ctl
launch` still stops at `/ping`, which is the right level for a process-control tool;
anything that then asserts on state should do what the runner does.

## The cell name that wasn't

The smoke test's first two runs failed on `player.cell == "WhiterunBanneredMare"`, which
came back as `""` for thirty seconds while `interior: true` and a Hulda-is-nearby check
both passed. Probing by hand couldn't reproduce it — the cell name resolved in two
seconds every time.

The variable was the test mod. `ModForgeNavmeshNoop.esp` overrides `CELL 0x0001605E`,
which is the Bannered Mare (90206 decimal — an arithmetic slip on that conversion is what
made an early check say the plugin didn't touch this cell at all), and it writes no `EDID`
subrecord. The runtime cell object takes the winning record's EditorID, so with the plugin
installed the name is blank while the FormID, the interior flag and the actor list are all
correct.

Two conclusions, both now in [QA-SCHEMA.md](QA-SCHEMA.md):

- **Assert on `cell_form_id`, not `cell`.** Any plugin in the load order can blank an
  EditorID by overriding a record without carrying `EDID` forward. FormIDs are engine
  identity.
- **ModForge writes CELL overrides without preserving EDID**, which is a defect in the
  generator, not in this harness. A navmesh-only edit should not cost the cell its name.

Worth saying plainly: this is the QA loop doing its job on the first real run. The mod
under test changed observable state in a way nobody intended, and the harness caught it.

## qa_mcp — the loop as MCP tools

Registered in `~/.claude.json` next to `housecarl`, so it loads for every session:

```jsonc
"skyrim-qa": {
  "type": "stdio",
  "command": ".../agent-bridge/client/qa_mcp.py",
  "env": { "MO2_ROOT": "...", "MO2_PROFILE": "Default" }
}
```

**A new session has to start before the tools appear** — MCP servers are connected at
startup, so registering it mid-session does nothing for that session.

| Tool | |
|---|---|
| `qa_status` | is the game up, is the profile safe to edit |
| `qa_state` | the `/state` snapshot — what assertions are written against |
| `qa_console` | run a console command |
| `qa_actor` | move beside or start dialogue with an actor selected by name/runtime FormID and cell/loaded scope |
| `qa_dialogue` | select a visible dialogue option by text/index/TopicInfo FormID, or close dialogue |
| `qa_message_box` | select a visible modal button by text/index, optionally guarded by exact message text |
| `qa_global` | read a TESGlobal by EditorID |
| `qa_wait` | poll JSON-path conditions until asynchronous game state converges |
| `qa_run` | execute a qa.json, return the report |

**What is deliberately not exposed: `install`, `uninstall`, `launch`, `kill`.** Each is one
Bash call, they happen a handful of times per session, and the chatty calls — the ones
that justify MCP at all — are `state` and `console`. A model that can end the user's game
session with a single tool call is worse ergonomics than one that has to type the command.
`qa_run` still performs all of them, but from a qa.json the user can read first.

Hand-rolled JSON-RPC rather than the `mcp` package, for the same reason the rest of this
directory is stdlib-only. The one rule that matters: **stdout carries protocol traffic and
nothing else.** A stray `print()` corrupts the stream and the client drops the connection
with no useful error, so diagnostics go to stderr. Two consequences in the code: `qa_run`
forces `interactive=False` (a runner blocking on `input()` here would hang the server with
no way to answer it), and notifications — messages with no `id`, like
`notifications/initialized` — get no response at all, because replying to one is a
protocol violation some clients disconnect over.
