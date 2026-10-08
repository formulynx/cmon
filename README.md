# cmux agent monitor

Per-workspace (or per-workspace-group) "all AI agents finished / input needed"
notifications for cmux, driven by Claude Code and Codex hooks. No daemon: every
hook invocation folds its event into a state file and acts on transitions.

```
install.sh [uninstall [--purge]]        install / remove the symlink and hook entries
bin/agent-monitor hook <claude|codex>   hook command (reads hook JSON on stdin)
bin/agent-monitor status                print the group/workspace tree once
bin/agent-monitor watch                 keep the tree on screen, redraw on each hook event
test/test_agent_monitor.py              self-check: python3 test/test_agent_monitor.py
test/test_install.sh                    self-check: bash test/test_install.sh
```

State dir: `~/.cmuxterm/agent-monitor/` (`state.json` plus a lock file; override
with `AGENT_MONITOR_DIR`). Nothing grows: every hook call rewrites `state.json`
pruned to the workspaces and groups cmux currently reports.

## Install

```
git clone <this repo> && cd cmux-hey
./install.sh              # link ~/.local/bin/agent-monitor, add hooks to Claude Code and Codex
./install.sh uninstall    # remove them again (--purge also deletes the state dir)
```

macOS and `jq` are required. The script links `bin/agent-monitor` into
`~/.local/bin`, then merges hook entries into `~/.claude/settings.json` and
`~/.codex/hooks.json`, keeping every other entry and backing each file up as
`<file>.bak-<timestamp>` before writing. Re-running it is a no-op.

What it registers (all with the command `~/.local/bin/agent-monitor hook <src>`):

| agent | events | note |
| --- | --- | --- |
| Claude Code | SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, SubagentStart, SubagentStop, Stop | user-level, so teammates in split panes get them too; async except SessionEnd (timeout 1 s) |
| Codex | SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, Stop | appended after existing entries so Codex's positional trust hashes stay valid |

Codex skips untrusted hooks: after installing, open `/hooks` once in a Codex TUI
and trust the new entries.

## Inspect

Nothing has to run. `agent-monitor status` prints a tree: each workspace group
with its aggregate state, its workspaces (name, state, sidebar lane), and under
each workspace its agent sessions (teammate name when the transcript carries
one, otherwise the session id prefix; source; status). `agent-monitor watch` keeps that tree in the terminal and redraws it
whenever a hook updates the state (kqueue on the state dir, zero CPU while
idle); Ctrl-C to stop.

## Semantics

| session event | session status |
| --- | --- |
| UserPromptSubmit, PostToolUse, PreToolUse (other tools), SubagentStart | running |
| PreToolUse AskUserQuestion / ExitPlanMode, PermissionRequest | needs_input |
| Stop with `session_crons` or `background_tasks` other than `teammate` non-empty | waiting |
| Stop with both empty | idle |
| SessionEnd, or the agent process (hook's parent pid) is gone | removed |

Workspace state = needs_input if any session needs input, else running if any
session is running, else idle (a `waiting` session is idle unless another
session in the same set is running: teammates are tracked as their own
sessions, so a lead parked on them is not work in flight). Each hook call
reconciles every workspace and group: it sets the sidebar lane (`working` /
`needs-attention` / `done` / `auto`) of any workspace whose lane changed, and
sends a `cmux notify` to the group anchor (or the workspace itself when
ungrouped) on `-> needs_input` and `running|needs_input -> idle`. Group
membership and workspace titles come from `cmux rpc workspace.group.list` and
`cmux --json workspace list`, cached for 60 s; sessions, lanes and notify state
for workspaces that no longer exist are deleted on each refresh.

Hooks are asynchronous and race for the state lock, so each event carries the
hook's start time and an event older than the session's last applied one is
ignored. The state is computed under the lock; `cmux` runs after it is released.
Teammates, Codex panes and the orchestrator are separate sessions in the same
workspace, so "idle" means all of them are idle.

## Known limits

- cmux keeps one unread notification per surface; a second notification within
  seconds replaces the first.
- A Codex launched through `codex app-server` / `codex --remote` runs hooks in
  the server process; verify it logs events before relying on it.
- A crashed agent is removed at the next hook event from any session, when its
  pid is found dead. A user interrupt (Esc) emits no hook, so a session stays
  `running` until its next event.
- Each hook call costs about 0.1 s (python startup); hooks are async so the
  agent is not delayed.
