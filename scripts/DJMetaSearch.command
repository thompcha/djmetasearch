#!/bin/zsh

set -u

APP_DIR="$HOME/Documents/Scripts/djmetasearch"
PYTHON="$APP_DIR/.venv/bin/python"
SEARCH_SCRIPT="$APP_DIR/standalone_app.py"
LOG_DIR="$HOME/Library/Logs/DJ Meta Search"
LOG_FILE="$LOG_DIR/launcher.log"
LAUNCH_TTY="$(tty 2>/dev/null || true)"

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

close_own_terminal_tab() {
  local tty_name="$1"
  if [[ "${TERM_PROGRAM:-}" != "Apple_Terminal" || "$tty_name" != /dev/* ]]; then
    return
  fi
  /usr/bin/osascript - "$tty_name" >/dev/null 2>&1 <<'APPLESCRIPT' &
on run argv
  delay 0.2
  set targetTTY to item 1 of argv
  tell application "Terminal"
    repeat with terminalWindow in windows
      repeat with terminalTab in tabs of terminalWindow
        if tty of terminalTab is targetTTY then
          if (count of tabs of terminalWindow) is 1 then
            close terminalWindow
          else
            close terminalTab
          end if
          return
        end if
      end repeat
    end repeat
  end tell
end run
APPLESCRIPT
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
  "$PYTHON" "$SEARCH_SCRIPT" "$1" >>"$LOG_FILE" 2>&1
else
  "$PYTHON" "$SEARCH_SCRIPT" >>"$LOG_FILE" 2>&1
fi
STATUS=$?
if (( STATUS == 0 )); then
  close_own_terminal_tab "$LAUNCH_TTY"
fi
exit "$STATUS"
