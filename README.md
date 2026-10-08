# cmon — Cmux agent MONitor

Per-workspace (or per-workspace-group) "all AI agents finished / input needed"
notifications for cmux, driven by Claude Code and Codex hooks. No daemon: every
hook invocation folds its event into a state file and acts on transitions.

## Why a separate tool

cmux's own agent notifications are per session: a turn-complete banner when
one Claude or Codex process stops, a permission prompt, an idle reminder. They
have no view across the sessions that share a workspace, so an orchestrator
that stops to wait for its teammates is reported as "finished" while the
teammates are still working, and a Codex pane in the same workspace is not
counted at all. There is also no aggregation per workspace group. This tool
keeps one state table for every agent session on the machine and reports the
workspace's and group's combined state instead (see Related cmux features).

```
install.sh [uninstall [--purge]]        install / remove the symlink and hook entries
bin/cmon hook <claude|codex>   hook command (reads hook JSON on stdin)
bin/cmon status                print the group/workspace tree once
bin/cmon watch                 keep the tree on screen, redraw on each hook event
test/test_agent_monitor.py              self-check: python3 test/test_agent_monitor.py
test/test_install.sh                    self-check: bash test/test_install.sh
```

State dir: `~/.cmuxterm/cmon/` (`state.json`, a lock file, and
`last-unattributed.json`, the most recent hook event that arrived without a
`CMUX_WORKSPACE_ID`; override with `CMON_DIR`). Nothing grows: every hook call rewrites `state.json`
pruned to the workspaces and groups cmux currently reports.

## Install

```
git clone <this repo> && cd cmon
./install.sh              # link ~/.local/bin/cmon, add hooks to Claude Code and Codex
./install.sh uninstall    # remove them again (--purge also deletes the state dir)
```

macOS and `jq` are required. The script links `bin/cmon` into
`~/.local/bin`, then merges hook entries into `~/.claude/settings.json` and
`~/.codex/hooks.json`, keeping every other entry and backing each file up as
`<file>.bak-<timestamp>` before writing. Re-running it is a no-op.

What it registers (all with the command `~/.local/bin/cmon hook <src>`):

| agent | events | note |
| --- | --- | --- |
| Claude Code | SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, SubagentStart, SubagentStop, Stop | user-level, so teammates in split panes get them too; async except SessionEnd (timeout 1 s) |
| Codex | SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, Stop | appended after existing entries so Codex's positional trust hashes stay valid |

Codex skips untrusted hooks: after installing, open `/hooks` once in a Codex TUI
and trust the new entries.

Moving or renaming the clone later breaks the symlink; run `./install.sh` again
from the new location. Upgrading from the pre-rename `agent-monitor` install is
handled by the same command (old symlink, hook entries and state dir are
replaced; Codex asks to trust the renamed entries again).

## Inspect

Nothing has to run. `cmon status` prints a tree: each workspace group
with its aggregate state, its workspaces (name, state, sidebar lane), and under
each workspace its agent sessions (teammate name when the transcript carries
one, otherwise the session id prefix; source; status). `cmon watch` keeps that tree in the terminal and redraws it
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
session is running or waiting (a background shell, monitor, subagent or
scheduled wakeup will resume a waiting session; teammates are tracked as
their own sessions and are not counted as the lead's background work), else
idle. Each hook call
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
- A Codex thread driven through `codex app-server` / `codex --remote` runs its
  hooks with a scrubbed environment (no `CMUX_*` variables). cmon then asks
  cmux which pane owns the hook's parent process (`cmux --json top`) and
  attributes the session to that pane's workspace, once per session. A hook
  whose parent is not in any cmux pane is dropped and recorded in
  `last-unattributed.json`.
- A crashed agent is removed at the next hook event from any session, when its
  pid is found dead. A user interrupt (Esc) emits no hook, so a session stays
  `running` until its next event.
- Codex hooks fire only once a thread exists: a TUI sitting at its first prompt
  is not visible until the first message is sent.
- A background shell the agent started and never waits on (a dev server left
  running) keeps its session `waiting`, so the workspace never reports idle
  until that task ends.
- Each hook call costs about 0.1 s (python startup); hooks are async so the
  agent is not delayed.

## Related cmux features

Checked against the cmux docs on `main` (agent-hooks, notifications, feed,
events, automations, workspace-groups, subagents-panel-plan, cmux.schema.json)
with cmux 0.64.22 installed. None of them provides a per-workspace or
per-group "all agents finished" signal:

- **`notifications.*` settings** (`agentTurnComplete` with `whenIdle`,
  `agentIdleReminder`, `agentPermissionPrompt`,
  `automation.suppressSubagentNotifications`): per session, driven by the hooks
  the cmux wrappers inject. `whenIdle` only holds the banner while that
  session's own background tasks run.
- **`notifications.hooks`**: shell hooks that rewrite an outgoing notification's
  effects (banner, sound, reorder, command). They receive `agent.category`
  (`turn-complete` / `needs-permission` / `idle-reminder`) and `pending`, and
  can filter, but never originate, a notification.
- **Automations** (`~/.cmuxterm/automations.json`, `cmux automation ...`):
  event-bus rules with `notify` / `rpc` / `run` / `webhook` actions; the
  documented trigger is `agent.needs_input`. Rules act on one event and keep
  no state, so aggregation still needs an external script. Not present in
  0.64.22 (`cmux automation` is unknown) nor named in the 0.65.0 release notes.
  Once available, a rule on `agent.hook.*` with `run: cmon hook ...`
  could replace the Claude/Codex hook entries this tool installs, if those
  events are exposed as triggers.
- **Agents panel** (custom sidebar `panel-subagents`, data context
  `workspaces[i].agents`): per-workspace list with working / idle / ended
  counts, display only, no notification; Codex teams hierarchy deferred.
- **Workspace groups**: structure and placement only, no aggregated state,
  hooks, or events.
- **`cmux events`**: `agent.hook.<Event>` and `feed.item.*` exist, but no
  "all idle" or "status changed" event.
