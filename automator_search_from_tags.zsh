#!/bin/zsh
set -euo pipefail

APP_DIR="$HOME/Documents/Scripts/djmetasearch"
PYTHON="$APP_DIR/.venv/bin/python"
SEARCH_SCRIPT="$APP_DIR/standalone_app.py"

if [[ ! -x "$PYTHON" ]]; then
  MESSAGE="DJ Meta Search is not set up. Double-click install.command in $APP_DIR, then try again."
  print -u2 -- "$MESSAGE"
  /usr/bin/osascript - "$MESSAGE" <<'APPLESCRIPT' 2>/dev/null || true
on run argv
  display alert "DJ Meta Search could not open" message (item 1 of argv) as critical
end run
APPLESCRIPT
  exit 2
fi

if [[ ! -f "$SEARCH_SCRIPT" ]]; then
  print -u2 "DJ Meta Search is missing: $SEARCH_SCRIPT"
  exit 2
fi

if (( $# == 0 )); then
  print -u2 "No input file provided."
  exit 1
fi

exec "$PYTHON" "$SEARCH_SCRIPT" --print-query "$1"
