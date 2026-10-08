#!/bin/bash
# install.sh [uninstall [--purge]]
# Links bin/cmon into ~/.local/bin and adds its hook entries to
# ~/.claude/settings.json and ~/.codex/hooks.json (idempotent: existing
# cmon entries are replaced, everything else is kept). `uninstall`
# reverses it; `--purge` also deletes the state dir. macOS + jq required.
set -euo pipefail
cd "$(dirname "$0")"
BIN="$HOME/.local/bin/cmon"
CLAUDE="$HOME/.claude/settings.json"
CODEX="$HOME/.codex/hooks.json"
STATE="$HOME/.cmuxterm/cmon"
CMD='~/.local/bin/cmon hook'
CLAUDE_EVENTS='"SessionStart","UserPromptSubmit","PreToolUse","PostToolUse","PermissionRequest","SubagentStart","SubagentStop","Stop"'
CODEX_EVENTS='"SessionStart","UserPromptSubmit","PreToolUse","PostToolUse","PermissionRequest","Stop"'

[ "$(uname)" = Darwin ] || { echo "macOS only (cmux)"; exit 1; }
command -v jq >/dev/null || { echo "jq is required (brew install jq)"; exit 1; }

# strip every hook group whose command is ours; keep the rest untouched
STRIP='.hooks |= with_entries(.value |= map(select(((.hooks // []) | any(.command | test("(agent-monitor|cmon) hook"))) | not)))'

write() {  # write <file> <jq-filter> : back up, apply, replace atomically
  local f="$1" filter="$2" tmp
  mkdir -p "$(dirname "$f")"
  [ -s "$f" ] || echo '{}' > "$f"
  tmp="$(mktemp)"
  jq "$filter" "$f" > "$tmp"
  if ! cmp -s "$f" "$tmp"; then
    cp "$f" "$f.bak-$(date +%Y%m%d%H%M%S)"
    mv "$tmp" "$f"
    echo "updated $f"
  else
    rm -f "$tmp"; echo "unchanged $f"
  fi
}

install() {
  mkdir -p "$(dirname "$BIN")"
  # migrate from the pre-rename install (agent-monitor)
  [ -L "$HOME/.local/bin/agent-monitor" ] && rm "$HOME/.local/bin/agent-monitor" && echo "removed $HOME/.local/bin/agent-monitor"
  [ -d "$HOME/.cmuxterm/agent-monitor" ] && rm -rf "$HOME/.cmuxterm/agent-monitor" && echo "removed $HOME/.cmuxterm/agent-monitor"
  ln -sfn "$PWD/bin/cmon" "$BIN"; echo "linked $BIN"
  write "$CLAUDE" ".hooks //= {} | $STRIP
    | reduce ($CLAUDE_EVENTS) as \$k (.; .hooks[\$k] += [{hooks:[{type:\"command\",command:\"$CMD claude\",async:true}]}])
    | .hooks.SessionEnd += [{hooks:[{type:\"command\",command:\"$CMD claude\",timeout:1}]}]"
  write "$CODEX" ".hooks //= {} | $STRIP
    | reduce ($CODEX_EVENTS) as \$k (.; .hooks[\$k] += [{hooks:[{type:\"command\",command:\"$CMD codex\",timeout:5}]}])
    | .hooks.SessionEnd += [{hooks:[{type:\"command\",command:\"$CMD codex\",timeout:1}]}]"
  cat <<MSG
done. Next:
  - Claude Code: hooks apply to new sessions.
  - Codex: open /hooks once in a Codex TUI and trust the new cmon entries (untrusted hooks are skipped).
  - Check:  cmon status   (after one agent turn inside cmux)
MSG
}

uninstall() {
  [ -L "$BIN" ] && { rm "$BIN"; echo "removed $BIN"; }
  [ -f "$CLAUDE" ] && write "$CLAUDE" "$STRIP"
  [ -f "$CODEX" ] && write "$CODEX" "$STRIP | .hooks |= with_entries(select(.value != []))"
  if [ "${1:-}" = "--purge" ]; then rm -rf "$STATE"; echo "removed $STATE"; fi
  echo "done. Any lane pinned by the monitor clears with: cmux workspace status set auto --workspace <id>"
}

case "${1:-install}" in
  install) install ;;
  uninstall) uninstall "${2:-}" ;;
  *) sed -n 2,6p "$0"; exit 1 ;;
esac
