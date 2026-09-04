from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError, sync_playwright

import standalone_app
from standalone_app import (
    page_has_audio_module,
    page_is_logged_out,
    evaluate_open_page,
    parse_byte_range,
    resolve_crate_api_key,
    resolve_login_credentials,
    search_djpoolrecords,
    try_auto_login,
    unique_destination,
)


def test_evaluate_open_page_treats_window_closure_as_normal_exit() -> None:
    class ClosedPage:
        @staticmethod
        def is_closed() -> bool:
            return True

        @staticmethod
        def evaluate(_expression: str, _argument: object) -> None:
            raise AssertionError("A closed page must not be evaluated")

    class ClosingPage:
        @staticmethod
        def is_closed() -> bool:
            return False

        @staticmethod
        def evaluate(_expression: str, _argument: object) -> None:
            raise PlaywrightError("Target page, context or browser has been closed")

    assert not evaluate_open_page(ClosedPage(), "ignored")
    assert not evaluate_open_page(ClosingPage(), "ignored")


def test_evaluate_open_page_does_not_hide_other_playwright_errors() -> None:
    class BrokenPage:
        @staticmethod
        def is_closed() -> bool:
            return False

        @staticmethod
        def evaluate(_expression: str, _argument: object) -> None:
            raise PlaywrightError("JavaScript callback failed")

    with pytest.raises(PlaywrightError, match="JavaScript callback failed"):
        evaluate_open_page(BrokenPage(), "ignored")


def test_unique_destination_preserves_name_and_adds_suffix(tmp_path: Path) -> None:
    assert unique_destination(tmp_path, "Artist - Song.mp3") == tmp_path / "Artist - Song.mp3"
    (tmp_path / "Artist - Song.mp3").touch()
    (tmp_path / "Artist - Song (1).mp3").touch()
    assert unique_destination(tmp_path, "Artist - Song.mp3") == tmp_path / "Artist - Song (2).mp3"


def test_unique_destination_strips_parent_components(tmp_path: Path) -> None:
    assert unique_destination(tmp_path, "../../unsafe.mp3") == tmp_path / "unsafe.mp3"


def test_media_byte_ranges() -> None:
    assert parse_byte_range("bytes=0-99", 1000) == (0, 99)
    assert parse_byte_range("bytes=500-", 1000) == (500, 999)
    assert parse_byte_range("bytes=-100", 1000) == (900, 999)
    assert parse_byte_range("bytes=1000-", 1000) is None
    assert parse_byte_range("not-a-range", 1000) is None


def test_rendered_page_state_overrides_stale_auth_cookie_hint() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            "<body class='non-logged-in'><a href='/djpoolrecords-user-login/'>Log in</a></body>"
        )
        assert page_is_logged_out(page)
        assert not page_has_audio_module(page)

        page.set_content(
            "<body class='logged-in'><input class='dpr-meili-input' placeholder='Search audio files...'></body>"
        )
        assert not page_is_logged_out(page)
        assert page_has_audio_module(page)
        browser.close()


def test_auto_login_fills_submits_and_remembers() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <form id="loginform">
              <input name="log">
              <input name="pwd" type="password">
              <input name="rememberme" type="checkbox">
              <input name="wp-submit" type="submit">
            </form>
            <script>
              document.querySelector('#loginform').addEventListener('submit', event => {
                event.preventDefault();
                document.body.dataset.submitted = 'yes';
              });
            </script>
            """
        )

        assert try_auto_login(page, "fixture-user", "fixture-password")
        assert page.locator("input[name='log']").input_value() == "fixture-user"
        assert page.locator("input[name='pwd']").input_value() == "fixture-password"
        assert page.locator("input[name='rememberme']").is_checked()
        assert page.locator("body").get_attribute("data-submitted") == "yes"
        browser.close()


def test_credentials_prefer_environment_then_keychain(monkeypatch) -> None:
    monkeypatch.setenv("DJPOOL_USER", "environment-user")
    monkeypatch.delenv("DJPOOL_PASS", raising=False)
    monkeypatch.setattr(
        standalone_app,
        "read_keychain_secret",
        lambda _service, account: {"username": "keychain-user", "password": "keychain-password"}[account],
    )

    assert resolve_login_credentials() == ("environment-user", "keychain-password")


def test_djfolders_key_prefers_environment_then_keychain(monkeypatch) -> None:
    monkeypatch.setenv("DJFOLDERS_API_KEY", "environment-key")
    monkeypatch.delenv("CRATE_SEARCH_API_KEY", raising=False)
    monkeypatch.setattr(
        standalone_app,
        "read_keychain_secret",
        lambda _service, _account: "keychain-key",
    )
    assert resolve_crate_api_key() == "environment-key"

    monkeypatch.delenv("DJFOLDERS_API_KEY")
    assert resolve_crate_api_key() == "keychain-key"


def test_djpool_search_encodes_spaces_as_percent_20() -> None:
    class Response:
        ok = True
        status = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"hits": []}

    class RequestContext:
        url = ""

        def get(self, url: str, **_kwargs: object) -> Response:
            self.url = url
            return Response()

    class Context:
        request = RequestContext()

    template = {
        "schema": 2,
        "url": "https://djpoolrecords.com/wp-json/dpr-search/v1/files",
        "nonce": "fixture123",
    }
    model = search_djpoolrecords(Context(), template, "post malone congratulations")
    assert model["count"] == 0
    assert model["results"] == []
    assert model["offset"] == 0
    assert "q=post%20malone%20congratulations" in Context.request.url
    assert "+" not in Context.request.url
