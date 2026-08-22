from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_required_user_facing_scripts_exist_and_are_executable() -> None:
    paths = [
        ROOT / "install.command",
        ROOT / "update.command",
        ROOT / "automator_search_from_tags.zsh",
        ROOT / "scripts" / "DJMetaSearch.command",
        ROOT / "scripts" / "Update DJ Meta Search.command",
    ]
    for path in paths:
        assert path.is_file(), path
        assert path.stat().st_mode & 0o111, path


def test_portable_scripts_do_not_contain_developer_home_path() -> None:
    paths = [
        ROOT / "install.command",
        ROOT / "update.command",
        ROOT / "automator_search_from_tags.zsh",
        ROOT / "scripts" / "DJMetaSearch.command",
        ROOT / "scripts" / "Update DJ Meta Search.command",
    ]
    for path in paths:
        developer_home = "/Users/" + "thompcha"
        assert developer_home not in path.read_text(encoding="utf-8")


def test_launcher_and_automator_invocations_match_cli_contract() -> None:
    launcher = (ROOT / "scripts" / "DJMetaSearch.command").read_text(encoding="utf-8")
    automator = (ROOT / "automator_search_from_tags.zsh").read_text(encoding="utf-8")
    assert '"$PYTHON" "$SEARCH_SCRIPT"' in launcher
    assert '"$PYTHON" "$SEARCH_SCRIPT" --print-query "$1"' in automator


def test_runtime_state_is_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "djpool_state.json" in ignore
    assert "bootstrap_cache.json" in ignore
    assert ".venv/" in ignore
