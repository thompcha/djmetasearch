#!/bin/zsh

set -eu
set -o pipefail

APP_NAME="DJ Meta Search"
REPO_DIR="$HOME/Documents/Scripts/djmetasearch"
LOG_DIR="$HOME/Library/Logs/$APP_NAME"
LOG_FILE="$LOG_DIR/update.log"

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
  show_dialog "$APP_NAME update failed" "$message\n\nDetails were saved to:\n$LOG_FILE"
  exit 1
}

trap 'fail "The update stopped unexpectedly. Open the log for details, then try again."' ZERR

print ""
print "=== $APP_NAME update: $(date) ==="

[[ "$(uname -s)" == "Darwin" ]] || fail "$APP_NAME supports macOS only."
[[ -d "$REPO_DIR" ]] || fail "The project was not found at $REPO_DIR. Install it there, then run the updater again."
[[ -f "$REPO_DIR/install.command" ]] || fail "The project folder is incomplete. Download it again before updating."

command -v git >/dev/null 2>&1 || fail "Git was not found. Install Apple’s Command Line Tools, or reinstall the project from a GitHub download."
git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "The project folder is not a Git checkout. Downloaded ZIP copies cannot use this updater; install a Git clone instead."

CHANGES="$(git -C "$REPO_DIR" status --porcelain --untracked-files=normal)"
if [[ -n "$CHANGES" ]]; then
  print -r -- "$CHANGES"
  fail "Local files have been changed. The updater left them untouched. Ask the developer to review the changes shown in the update log."
fi

git -C "$REPO_DIR" remote get-url origin >/dev/null 2>&1 || fail "This checkout has no GitHub origin. Ask the developer to repair or reinstall it."

BEFORE="$(git -C "$REPO_DIR" rev-parse HEAD)"
print "Checking the stable main branch for updates..."
git -C "$REPO_DIR" pull --ff-only origin main || fail "Git could not safely update the main branch. Nothing was discarded. Check the internet connection or ask the developer for help."
AFTER="$(git -C "$REPO_DIR" rev-parse HEAD)"

print "Refreshing dependencies and Spotlight launchers..."
DJMETASEARCH_NO_SUCCESS_DIALOG=1 "$REPO_DIR/install.command" || fail "The code was downloaded, but setup could not be refreshed. Run install.command manually."

trap - ZERR
if [[ "$BEFORE" == "$AFTER" ]]; then
  RESULT="DJ Meta Search is already up to date."
else
  RESULT="DJ Meta Search was updated successfully."
fi
print "$RESULT"
show_dialog "$APP_NAME update" "$RESULT"
