# client/ — the Linux half

`agent-bridge` (the DLL) is the eyes and hands inside the game. This directory is
everything on the Linux side of that socket. Kept in the same subproject on purpose:
the port number, the `/state` field names and the client that reads them are one
contract, and a two-sided edit should be one commit.

| Tool | Status | What it does |
|---|---|---|
| `mo2ctl.py` | ✅ verified end-to-end; `launch` uses the deployment script | Drive MO2 without its GUI: install / uninstall / enable / disable / launch / kill / status |
| `bridge.py` | ✅ | Talking to the in-game HTTP bridge. Owns the port; everything else imports it |
| `actor_value_probe.py` | ✅ offline-tested | Strict `getav` reader: exact engine output shape plus repeated identical reads for a chosen runtime reference |
| `qa_runner.py` | ✅ semantic paths and baseline manifest/fingerprint gate runtime-verified | Execute a `qa.json`, including fail-closed baseline loading and retryable actor/dialogue/MessageBox steps, and report pass/fail. Schema: [QA-SCHEMA.md](QA-SCHEMA.md) |
| `qa_mcp.py` | ✅ 0.3.0 runtime-verified; 0.4.0 MessageBox tool offline-tested; client restart required to discover new tool schemas | MCP server: state, console, actor, dialogue, MessageBox, global, structured wait, and whole-run tools |

stdlib only, no venv.

使用方式見 [client 手冊](docs/README.md)。

## mo2ctl

詳見 [手冊](docs/mo2ctl.md)。

### Verified end-to-end

詳見 [手冊](docs/baseline.md)。

## Three things that are not obvious

詳見 [手冊](docs/pitfalls.md)。

## launch

詳見 [手冊](docs/launch.md)。

## The cell name that wasn't

詳見 [手冊](docs/pitfalls.md#the-cell-name-that-wasnt)。

## qa_mcp — the loop as MCP tools

詳見 [手冊](docs/mcp.md)。
