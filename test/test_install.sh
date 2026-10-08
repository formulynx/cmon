#!/bin/bash
# Self-check for install.sh against a throwaway HOME. Run: bash test/test_install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export HOME="$(mktemp -d)"
mkdir -p "$HOME/.claude" "$HOME/.codex"
# pre-existing config that must survive: a foreign SubagentStop hook, a foreign codex Stop hook at index 0
echo '{"model":"x","hooks":{"SubagentStop":[{"hooks":[{"type":"command","command":"echo other"}]}],"PostToolUse":[]}}' > "$HOME/.claude/settings.json"
echo '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"/x/cmux-stop.sh","timeout":5}]}]}}' > "$HOME/.codex/hooks.json"

./install.sh >/dev/null
[ "$(readlink "$HOME/.local/bin/agent-monitor")" = "$PWD/bin/agent-monitor" ] || { echo "FAIL symlink"; exit 1; }
j() { jq -r "$1" "$HOME/.claude/settings.json"; }
c() { jq -r "$1" "$HOME/.codex/hooks.json"; }
[ "$(j '.model')" = x ] || { echo "FAIL other settings kept"; exit 1; }
[ "$(j '.hooks.SubagentStop | length')" = 2 ] && [ "$(j '.hooks.SubagentStop[0].hooks[0].command')" = "echo other" ] || { echo "FAIL foreign hook kept first"; exit 1; }
[ "$(j '.hooks.Stop[0].hooks[0].async')" = true ] && [ "$(j '.hooks.SessionEnd[0].hooks[0].timeout')" = 1 ] || { echo "FAIL claude entries"; exit 1; }
[ "$(j '[.hooks[][] | .hooks[].command | select(test("agent-monitor"))] | length')" = 9 ] || { echo "FAIL claude count"; exit 1; }
[ "$(c '.hooks.Stop[0].hooks[0].command')" = /x/cmux-stop.sh ] && [ "$(c '.hooks.Stop[1].hooks[0].command')" = '~/.local/bin/agent-monitor hook codex' ] || { echo "FAIL codex append after existing"; exit 1; }
[ "$(c '.hooks.SessionEnd[0].hooks[0].timeout')" = 1 ] || { echo "FAIL codex SessionEnd"; exit 1; }
ls "$HOME/.claude/settings.json.bak-"* "$HOME/.codex/hooks.json.bak-"* >/dev/null || { echo "FAIL backups"; exit 1; }

snap="$(cat "$HOME/.claude/settings.json")"; ./install.sh >/dev/null
[ "$snap" = "$(cat "$HOME/.claude/settings.json")" ] || { echo "FAIL idempotent"; exit 1; }

mkdir -p "$HOME/.cmuxterm/agent-monitor"; touch "$HOME/.cmuxterm/agent-monitor/state.json"
./install.sh uninstall --purge >/dev/null
[ ! -e "$HOME/.local/bin/agent-monitor" ] || { echo "FAIL unlink"; exit 1; }
[ "$(j '[.hooks[][] | .hooks[].command | select(test("agent-monitor"))] | length')" = 0 ] || { echo "FAIL claude strip"; exit 1; }
[ "$(j '.hooks.SubagentStop[0].hooks[0].command')" = "echo other" ] && [ "$(j '.hooks.PostToolUse | length')" = 0 ] || { echo "FAIL claude others kept"; exit 1; }
[ "$(c '.hooks | keys | join(",")')" = Stop ] && [ "$(c '.hooks.Stop | length')" = 1 ] || { echo "FAIL codex strip"; exit 1; }
[ ! -e "$HOME/.cmuxterm/agent-monitor" ] || { echo "FAIL purge"; exit 1; }
echo ok
