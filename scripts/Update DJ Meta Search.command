#!/bin/zsh

set -u

UPDATER="$HOME/Documents/Scripts/djmetasearch/update.command"

if [[ ! -x "$UPDATER" ]]; then
  MESSAGE="The updater is missing from $UPDATER. Reinstall DJ Meta Search, then try again."
  print -u2 -- "$MESSAGE"
  if command -v osascript >/dev/null 2>&1; then
    /usr/bin/osascript - "$MESSAGE" <<'APPLESCRIPT'
on run argv
  display alert "DJ Meta Search could not update" message (item 1 of argv) as critical buttons {"OK"} default button "OK"
end run
APPLESCRIPT
  fi
  exit 1
fi

exec "$UPDATER"
