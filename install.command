#!/bin/zsh

set -eu
set -o pipefail

APP_NAME="DJ Meta Search"
REPO_DIR="${0:A:h}"
LOG_DIR="$HOME/Library/Logs/$APP_NAME"
LOG_FILE="$LOG_DIR/install.log"
APPLICATIONS_DIR="$HOME/Applications"
PYTHON_MINIMUM="3.9"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

show_dialog() {
  local title="$1"
  local message="$2"
  if [[ "${DJMETASEARCH_SKIP_DIALOGS:-0}" == "1" ]] || ! command -v osascript >/dev/null 2>&1; then
    return
  fi
  /usr/bin/osascript - "$title" "$message" <<'APPLESCRIPT'
on run argv
  display dialog (item 2 of argv) with title (item 1 of argv) buttons {"OK"} default button "OK"
end run
APPLESCRIPT
}

fail() {
  local message="$1"
  print -u2 "ERROR: $message"
  show_dialog "$APP_NAME installation failed" "$message\n\nDetails were saved to:\n$LOG_FILE"
  exit 1
}

trap 'fail "Setup stopped unexpectedly. Open the log for details, then try install.command again."' ZERR

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "$APP_NAME supports macOS only."
fi

if [[ ! -f "$REPO_DIR/standalone_app.py" || ! -f "$REPO_DIR/requirements.txt" ]]; then
  fail "The installer is not inside a complete DJ Meta Search folder. Download the project again, then run install.command from that folder."
fi

python_is_supported() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

find_python() {
  local candidate
  if [[ -n "${DJMETASEARCH_PYTHON:-}" ]] && [[ -x "$DJMETASEARCH_PYTHON" ]] && python_is_supported "$DJMETASEARCH_PYTHON"; then
    print -r -- "$DJMETASEARCH_PYTHON"
    return 0
  fi
  for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_supported "$(command -v "$candidate")"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

print ""
print "=== $APP_NAME installation: $(date) ==="
print "Project: $REPO_DIR"

SYSTEM_PYTHON="$(find_python)" || fail "Python $PYTHON_MINIMUM or newer was not found. Install a current Python 3 release from python.org, then double-click install.command again."
VENV_PYTHON="$REPO_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  print "Creating the private Python environment with $SYSTEM_PYTHON..."
  "$SYSTEM_PYTHON" -m venv "$REPO_DIR/.venv" || fail "Python could not create the private environment. Reinstall Python from python.org and try again."
fi

if ! python_is_supported "$VENV_PYTHON"; then
  fail "The existing .venv uses an unsupported Python. Move the .venv folder to the Trash, then run install.command again."
fi

print "Installing required Python packages..."
"$VENV_PYTHON" -m pip install -r "$REPO_DIR/requirements.txt" || fail "Required packages could not be installed. Check the internet connection and try again."

print "Installing the private Chromium browser..."
"$VENV_PYTHON" -m playwright install chromium || fail "The Chromium browser could not be installed. Check the internet connection and try again."

mkdir -p "$APPLICATIONS_DIR"
cp "$REPO_DIR/scripts/DJ Meta Search.command" "$APPLICATIONS_DIR/DJ Meta Search.command"
cp "$REPO_DIR/scripts/Update DJ Meta Search.command" "$APPLICATIONS_DIR/Update DJ Meta Search.command"
chmod 755 "$APPLICATIONS_DIR/DJ Meta Search.command" "$APPLICATIONS_DIR/Update DJ Meta Search.command"

print "Installed Spotlight launchers in $APPLICATIONS_DIR"
print "Installation completed successfully."
trap - ZERR

if [[ "${DJMETASEARCH_NO_SUCCESS_DIALOG:-0}" != "1" ]]; then
  show_dialog "$APP_NAME is ready" "Open Spotlight and type “DJ Meta Search” to launch it. Use “Update DJ Meta Search” when you want updates."
fi
