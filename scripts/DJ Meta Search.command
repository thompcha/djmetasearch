#!/bin/zsh

set -u

APP_DIR="$HOME/Documents/Scripts/djmetasearch"
PYTHON="$APP_DIR/.venv/bin/python"
SEARCH_SCRIPT="$APP_DIR/standalone_app.py"
LOG_DIR="$HOME/Library/Logs/DJ Meta Search"
LOG_FILE="$LOG_DIR/launcher.log"

show_error() {
  local message="$1"
  print -u2 -- "$message"
  if command -v osascript >/dev/null 2>&1; then
    /usr/bin/osascript - "$message" <<'APPLESCRIPT'
on run argv
  display alert "DJ Meta Search could not open" message (item 1 of argv) as critical buttons {"OK"} default button "OK"
end run
APPLESCRIPT
  fi
}

if [[ ! -f "$SEARCH_SCRIPT" ]]; then
  show_error "The project is missing from $APP_DIR. Install DJ Meta Search there, then run install.command."
  exit 1
fi
if [[ ! -x "$PYTHON" ]]; then
  show_error "The private Python environment is missing. Double-click install.command inside $APP_DIR, then try again."
  exit 1
fi

mkdir -p "$LOG_DIR"
if (( $# > 0 )); then
  exec "$PYTHON" "$SEARCH_SCRIPT" "$1" >>"$LOG_FILE" 2>&1
fi
exec "$PYTHON" "$SEARCH_SCRIPT" >>"$LOG_FILE" 2>&1
