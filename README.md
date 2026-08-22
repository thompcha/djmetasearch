# DJ Meta Search

DJ Meta Search is a macOS search window for DJPoolRecords and RVRemix. It
combines both providers, previews media locally, and downloads files through the
existing metadata-cleanup workflow.

## One-time installation

DJ Meta Search requires macOS and Python 3.9 or newer. A current installer is
available from [python.org](https://www.python.org/downloads/macos/) if the Mac
does not already have Python 3.

Open **Terminal**, paste these three lines, and press Return:

```zsh
mkdir -p "$HOME/Documents/Scripts"
git clone https://github.com/thompcha/djmetasearch.git "$HOME/Documents/Scripts/djmetasearch"
open "$HOME/Documents/Scripts/djmetasearch/install.command"
```

If macOS offers to install its Command Line Developer Tools, accept, then paste
the commands again. The installer creates a private Python environment, installs
the required browser, and puts two launchers in `~/Applications`. It can safely
be rerun: it does not replace saved login state or downloaded music.

If a macOS security warning prevents the installer from opening, Control-click
`install.command` in Finder, choose **Open**, and confirm once.

## Launch through Spotlight

Press Command-Space, type **DJMetaSearch**, and press Return. Starting without
a selected file is supported; the window opens with an empty search field.
The launcher closes its own Terminal tab after the search window exits normally;
it leaves the tab open when startup fails so the error remains available.

DJPoolRecords may show its normal login or browser challenge the first time.
RVRemix needs no saved login. Downloads go to `~/Downloads`.

## Update through Spotlight

Press Command-Space, type **Update DJ Meta Search**, and press Return. The
updater only fast-forwards from the stable `main` branch. It refuses to overwrite
local changes, then refreshes dependencies and both Spotlight launchers.

The updater requires the Git clone at
`~/Documents/Scripts/djmetasearch`. A copy installed from a downloaded ZIP can
run the app, but cannot receive updates this way.

## Automator: search from a selected audio file

This Quick Action passes the selected file **as an argument**, not through
standard input. Its canonical shell code is
[`automator_search_from_tags.zsh`](automator_search_from_tags.zsh).

1. Open **Automator** and choose **Quick Action** as the document type.
2. At the top, set “Workflow receives current” to **files or folders** in
   **Finder**.
3. Add the **Run Shell Script** action.
4. Set Shell to `/bin/zsh` and “Pass input” to **as arguments**.
5. Paste this code into the action:

   ```zsh
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
   ```

6. Save it as **Search with DJ Meta Search**.
7. In Finder, select an audio file—even one whose name contains spaces—then
   Control-click it and choose **Quick Actions > Search with DJ Meta Search**.

If the project or `.venv` is missing, open
`~/Documents/Scripts/djmetasearch` and double-click `install.command`.

An exported Automator workflow is not included because Automator exports can
carry creator-machine metadata. The short setup above keeps the portable shell
wrapper as the single source of truth.

## Troubleshooting

- Installer details: `~/Library/Logs/DJ Meta Search/install.log`
- Updater details: `~/Library/Logs/DJ Meta Search/update.log`
- Launcher details: `~/Library/Logs/DJ Meta Search/launcher.log`
- **Python was not found:** install Python 3 from python.org and rerun the
  installer.
- **The private Python environment is missing:** rerun `install.command`.
- **The updater found local changes:** it has not discarded anything. Send the
  update log to the developer for review.
- **DJPoolRecords asks for a login:** complete its normal login in the browser
  window. No password is stored in this repository.

## Authentication and local state

The app writes `djpool_state.json` and `bootstrap_cache.json` beside the source
code. Both are ignored by Git and preserved by installation and updates. The
first is the DJPoolRecords browser session; the second contains a temporary REST
endpoint and nonce, not a password. Queries and search results stay in memory,
and preview files are deleted when the app exits.

Credentials may optionally come from `DJPOOL_USER` and `DJPOOL_PASS`, or from
the macOS Keychain service `djpoolrecords` with accounts named `username` and
`password`. Otherwise, use the visible interactive login.

## Developer setup

The dependency manifest is `requirements.txt`; Python 3.9+ is required.

```zsh
./install.command
.venv/bin/python -m pytest -q
```

Useful direct invocations:

```zsh
.venv/bin/python standalone_app.py
.venv/bin/python standalone_app.py "American Breed - Bend Me Shape Me"
.venv/bin/python standalone_app.py --query-from-tags "/path/with spaces/track.mp3"
.venv/bin/python standalone_app.py --downloads-dir "/path/to/folder"
```

Before publishing an enhancement, review every intended file and run the tests:

```zsh
git status
git add <intentional files>
git commit -m "Brief description of the enhancement"
git push
```

Do not use `git add .` without reviewing the untracked files. The friend updates
only from `main`; keep unfinished work on another branch.

## What the application does

- Queries DJPoolRecords through its authenticated audio-search endpoint and
  RVRemix through its public LetsBox search widget.
- Converts `Artist - Title` to a focused RVRemix query and filters irrelevant or
  duplicate results locally.
- Loads DJPoolRecords pages progressively while RVRemix runs in the background.
- Labels and priority-sorts merged results, previews media through a temporary
  session cache, and manages downloads without navigating away from the app.
- Cleans supported MP3/M4A tags and filenames. If cleanup fails, it preserves the
  original downloaded bytes and reports a warning.

Implementation details and trust boundaries are documented in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## License

DJ Meta Search is available under the [MIT License](LICENSE).
