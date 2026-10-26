# Three things that are not obvious

[手冊入口](README.md)；下列 CLI 範例以 `client/` 為工作目錄。

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

Two conclusions, both now in [QA-SCHEMA.md](../QA-SCHEMA.md):

- **Assert on `cell_form_id`, not `cell`.** Any plugin in the load order can blank an
  EditorID by overriding a record without carrying `EDID` forward. FormIDs are engine
  identity.
- **ModForge writes CELL overrides without preserving EDID**, which is a defect in the
  generator, not in this harness. A navmesh-only edit should not cost the cell its name.

Worth saying plainly: this is the QA loop doing its job on the first real run. The mod
under test changed observable state in a way nobody intended, and the harness caught it.
