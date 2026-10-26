# qa_mcp — the loop as MCP tools

[手冊入口](README.md)；下列 CLI 範例以 `client/` 為工作目錄。

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
