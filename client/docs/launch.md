# launch

[手冊入口](README.md)；下列 CLI 範例以 `client/` 為工作目錄。

```
<deployment>/instance/tools/launch-mo2.sh --shortcut SKSE
```

`launch` does not build the Proton command line itself. It shells out to the deployment's
own launcher script, resolved as `<repo>/../../instance/tools/launch-mo2.sh` and
overridable with `MO2CTL_LAUNCH_SCRIPT`; a missing script is a `Fail`, not a silent
fallback. The deployment script owns the prefix-version guard, the non-ASCII `modlist.txt`
guard and the `STEAM_COMPAT_*` environment.

MO2 still has to run inside the game's own Proton prefix — usvfs needs MO2 and the game
in one wine session, which is also why there is no separate MO2 wine prefix to point at.
`moshortcut://:SKSE` is MO2's own name for the `customExecutables` entry in
`ModOrganizer.ini`, so this is the same path the GUI's Run button takes. Use `--shortcut`
if that entry is renamed.

`launch` then polls `/ping` rather than guessing at a sleep, and gives up with
`bridge.reachable: false` instead of hanging. Observed cold start on this load order:
16–30s.

`--background-active` temporarily writes `bAlwaysActive=1` under `[General]` in the
selected profile's `skyrim.ini`. This keeps Skyrim's game thread draining while its window
is unfocused, which is required for a genuinely unattended main-menu `load` or `coc`.
The exact original bytes are kept under the profile's ignored `.mo2ctl-backups/` folder;
`mo2ctl kill` restores them, including when the game already exited, and the next launch
recovers a stale backup before making a new one. `qa_runner` enables this mode by default.

**`/ping` answering does not mean the game is ready.** `/ping` is served on the socket
thread and keeps answering right through load screens — deliberately, so a runner can
tell "process alive, game busy" from "process dead". The first `/state` after launch
reliably 503s with `game thread did not respond in time`. `qa_runner` also waits for `/state`. `mo2ctl
launch` still stops at `/ping`, which is the right level for a process-control tool;
anything that then asserts on state should do what the runner does.

Without background-active mode, losing window focus pauses Skyrim's game thread on this
host. A queued console request can first return a timeout and then execute when focus
returns, so callers must not blindly resend a timed-out mutating command. The QA runner
avoids that ambiguous state by keeping the engine active in the background.
