#!/usr/bin/env python3
"""Launch the combined DJPoolRecords and RVRemix search experience."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

try:
    from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page, Route, sync_playwright
except ImportError as exc:  # pragma: no cover - exercised before dependencies exist
    print(f"Missing dependency: playwright ({exc})", file=sys.stderr)
    print("Install with: python -m pip install -r requirements.txt && python -m playwright install chromium", file=sys.stderr)
    raise SystemExit(2)

from djmetasearch.cleanup import CleanupResult, process_download, unique_destination
from djmetasearch.cache import load_cache, make_cache, save_cache, validate_cache
from djmetasearch.query import default_query, resolve_query
from djmetasearch.results import (
    filter_djpool_model,
    merge_result_models,
    parse_search_payload,
    safe_preview_url,
    safe_remote_url,
)
from djmetasearch.rvremix import PAGE_URL as RVREMIX_URL, RVRemixClient

ROOT = Path(__file__).resolve().parent
UI_PATH = ROOT / "ui" / "app.html"
STATE_PATH = ROOT / "djpool_state.json"
CACHE_PATH = ROOT / "bootstrap_cache.json"
ORIGINAL_STATE_PATH = ROOT.parent / "djpoolrecords" / "djpool_state.json"

BASE_URL = "https://djpoolrecords.com/"
APP_URL = "https://djpoolrecords.com/__djmetasearch__/"
MEDIA_URL_PREFIX = "https://djpoolrecords.com/__djmetasearch_media__/"
LOGIN_URL = "https://djpoolrecords.com/djpoolrecords-user-login/"
SEARCH_URL = "https://djpoolrecords.com/wp-json/dpr-search/v1/files"
AUDIO_SELECTOR = "input.dpr-meili-input[placeholder='Search audio files...']"
KEYCHAIN_SERVICE = "djpoolrecords"
MEDIA_TYPES = {
    "audio": "audio/mpeg",
    "video": "video/mp4",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search DJPoolRecords and RVRemix together.")
    parser.add_argument("input_value", nargs="?", default="", help="Optional query or audio file path")
    parser.add_argument("--literal-query", action="store_true", help="Treat input as a literal query")
    parser.add_argument("--query-from-tags", action="store_true", help="Use artist/title audio tags when possible")
    parser.add_argument("--print-query", action="store_true", help="Print the transformed initial query")
    parser.add_argument(
        "--downloads-dir",
        default=str(Path.home() / "Downloads"),
        help="Destination for completed downloads (default: ~/Downloads)",
    )
    return parser.parse_args()


def import_initial_state() -> bool:
    """Copy existing auth state once; never write back to the original app."""
    if STATE_PATH.exists() or not ORIGINAL_STATE_PATH.exists():
        return False
    shutil.copy2(ORIGINAL_STATE_PATH, STATE_PATH)
    return True


def format_download_result(result: CleanupResult) -> tuple[str, bool]:
    """Build the shared completion notification after final placement."""
    if result.warning:
        return f"Downloaded unchanged: {result.final_path.name}. {result.warning}", True
    if result.changed:
        return f"Downloaded and cleaned: {result.final_path.name}", False
    return f"Downloaded: {result.final_path.name}", False


def complete_download(download: object, downloads_dir: Path) -> tuple[str, bool]:
    """Wait for a download, clean/place it, then build its completion notification."""
    suggested = getattr(download, "suggested_filename", "") or "download.bin"
    staged = download.path()
    if not staged:
        raise RuntimeError("Playwright did not provide the completed download path.")
    return format_download_result(process_download(Path(staged), suggested, downloads_dir))


def cached_download_filename(filename: str, mime_type: str, kind: str) -> str:
    """Give an extensionless result name the extension represented by its cached media."""
    safe_name = Path(filename or "download").name
    if Path(safe_name).suffix:
        return safe_name
    normalized_type = mime_type.split(";", 1)[0].strip().lower()
    extensions = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/aac": ".aac",
        "video/mp4": ".mp4",
    }
    extension = extensions.get(normalized_type, ".mp4" if kind == "video" else ".mp3")
    return f"{safe_name}{extension}"


def complete_cached_preview_download(
    record: dict[str, object], filename: str, downloads_dir: Path
) -> tuple[str, bool]:
    """Copy an already-cached preview through the normal cleanup/placement pipeline."""
    cached_path = record.get("path")
    if not isinstance(cached_path, Path) or not cached_path.is_file():
        raise RuntimeError("The cached preview is no longer available.")
    suggested = cached_download_filename(
        filename,
        str(record.get("mime_type") or ""),
        str(record.get("kind") or "audio"),
    )
    return format_download_result(process_download(cached_path, suggested, downloads_dir))


def has_wordpress_session(context: BrowserContext) -> bool:
    try:
        return any(cookie.get("name", "").startswith("wordpress_logged_in_") for cookie in context.cookies())
    except Exception:
        return False


def read_keychain_secret(service: str, account: str) -> str:
    if sys.platform != "darwin" or not service:
        return ""
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                service,
                "-a",
                account,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.rstrip("\r\n") if result.returncode == 0 else ""


def resolve_login_credentials() -> tuple[str, str]:
    username = os.environ.get("DJPOOL_USER", "")
    password = os.environ.get("DJPOOL_PASS", "")
    if not username:
        username = read_keychain_secret(KEYCHAIN_SERVICE, "username")
    if not password:
        password = read_keychain_secret(KEYCHAIN_SERVICE, "password")
    return username, password


def page_has_audio_module(page: Page) -> bool:
    try:
        return page.locator(AUDIO_SELECTOR).count() > 0
    except Exception:
        return False


def evaluate_open_page(page: Page, expression: str, argument: object = None) -> bool:
    """Evaluate a UI callback, treating a concurrently closed window as normal exit."""
    if page.is_closed():
        return False
    try:
        page.evaluate(expression, argument)
    except PlaywrightError as exc:
        if page.is_closed() or "Target page, context or browser has been closed" in str(exc):
            return False
        raise
    return True


def page_is_logged_out(page: Page) -> bool:
    """Use rendered state instead of trusting a possibly stale WordPress cookie."""
    try:
        body_classes = set((page.locator("body").get_attribute("class") or "").split())
        if "non-logged-in" in body_classes:
            return True
        if page.locator("form#loginform, form[action*='login']").count() > 0:
            return True
        return page.locator(f"a[href*='{urlsplit(LOGIN_URL).path}']").count() > 0
    except Exception:
        return False


def page_is_login_page(page: Page) -> bool:
    try:
        return urlsplit(page.url).path.rstrip("/") == urlsplit(LOGIN_URL).path.rstrip("/")
    except Exception:
        return False


def try_auto_login(page: Page, username: str, password: str) -> bool:
    """Fill and submit the rendered login form without persisting credentials."""
    if not username or not password:
        return False
    user_selectors = [
        "input[name='log']",
        "input[name='username']",
        "input[name='user_login']",
        "input[type='email']",
        "input#username",
    ]
    password_selectors = [
        "input[name='pwd']",
        "input[name='password']",
        "input[type='password']",
        "input#password",
    ]
    submit_selectors = [
        "input[name='wp-submit']",
        "button[name='wp-submit']",
        "#wp-submit",
        "button[type='submit']",
        "input[type='submit']",
    ]

    user_field = next((page.locator(selector).first for selector in user_selectors if page.locator(selector).count()), None)
    password_field = next(
        (page.locator(selector).first for selector in password_selectors if page.locator(selector).count()),
        None,
    )
    if user_field is None or password_field is None:
        return False
    user_field.fill(username)
    password_field.fill(password)
    remember = page.locator("input[name='rememberme']")
    if remember.count() and not remember.first.is_checked():
        remember.first.check()
    submit = next((page.locator(selector).first for selector in submit_selectors if page.locator(selector).count()), None)
    if submit is not None:
        submit.click()
    else:
        password_field.press("Enter")
    return True


def wait_for_interactive_auth(page: Page, context: BrowserContext, app_page: Page) -> bool:
    """Show only the temporary login/challenge tab when interaction is required."""
    try:
        username, password = resolve_login_credentials()
        auto_login_attempted = False
        if page_is_logged_out(page) or (not has_wordpress_session(context) and not page_has_audio_module(page)):
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
        if page_is_login_page(page):
            auto_login_attempted = try_auto_login(page, username, password)
            if auto_login_attempted:
                print("Submitted DJPoolRecords credentials from macOS Keychain or environment.")
        page.bring_to_front()
        if auto_login_attempted:
            print("Complete any DJPoolRecords browser challenge in the temporary tab if prompted.")
        else:
            print("Complete DJPoolRecords login or the browser challenge in the temporary tab.")
        deadline = time.time() + 300
        returned_home = False
        while time.time() < deadline and not page.is_closed():
            if page_has_audio_module(page):
                app_page.bring_to_front()
                return True
            logged_out = page_is_logged_out(page)
            if logged_out and not page_is_login_page(page):
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
                if not auto_login_attempted:
                    auto_login_attempted = try_auto_login(page, username, password)
                returned_home = False
                continue
            if not logged_out and has_wordpress_session(context) and not returned_home:
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
                returned_home = True
                continue
            try:
                page.wait_for_timeout(500)
            except Exception:
                break
        app_page.bring_to_front()
    except Exception:
        if not app_page.is_closed():
            app_page.bring_to_front()
    return False


def capture_bootstrap(context: BrowserContext, app_page: Page, _query: str) -> dict[str, object]:
    """Ensure the REST audio search is visible in an authenticated session."""
    page = context.new_page()
    app_page.bring_to_front()
    try:
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception:
            pass
        if not page_has_audio_module(page):
            if not wait_for_interactive_auth(page, context, app_page):
                raise RuntimeError("Authentication was not completed within five minutes.")
            try:
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
            except Exception:
                pass
        if not page_has_audio_module(page):
            raise RuntimeError("The authenticated DJPoolRecords audio search was not found.")
        search_config = page.evaluate(
            """() => window.dprMeili ? {
                url: String(window.dprMeili.filesRest || ""),
                nonce: String(window.dprMeili.nonce || "")
            } : null"""
        )
        if not isinstance(search_config, dict):
            raise RuntimeError("DJPoolRecords did not expose its audio search configuration.")
        cache = make_cache(
            str(search_config.get("url") or ""),
            str(search_config.get("nonce") or ""),
        )
        save_cache(CACHE_PATH, cache)
        if has_wordpress_session(context):
            context.storage_state(path=str(STATE_PATH))
        return cache
    finally:
        if not page.is_closed():
            page.close()
        if not app_page.is_closed():
            app_page.bring_to_front()


def rewrite_remote_headers(route: Route) -> None:
    headers = dict(route.request.headers)
    if urlsplit(route.request.url).hostname == "rvremix.com":
        headers["referer"] = RVREMIX_URL
        headers["origin"] = "https://rvremix.com"
    else:
        headers["referer"] = BASE_URL
        headers["origin"] = "https://djpoolrecords.com"
    try:
        route.continue_(headers=headers)
    except Exception:
        try:
            route.abort()
        except Exception:
            pass


def search_djpoolrecords(
    context: BrowserContext,
    template: object,
    query: str,
    offset: int = 0,
) -> dict[str, object]:
    """Fetch one authenticated page from the DPR REST audio search."""
    validated = validate_cache(template)
    if validated is None:
        raise RuntimeError("The DJPoolRecords search configuration is missing or expired.")
    parameters = urlencode(
        {"q": query, "limit": "50", "offset": str(max(0, offset))},
        quote_via=quote,
    )
    url = f"{validated['url']}?{parameters}"
    response = context.request.get(
        url,
        headers={
            "Accept": "application/json",
            "X-WP-Nonce": str(validated["nonce"]),
            "Referer": BASE_URL,
        },
        timeout=45_000,
    )
    if not response.ok:
        raise RuntimeError(f"DJPoolRecords returned HTTP {response.status}.")
    payload = response.json()
    model = filter_djpool_model(parse_search_payload(payload), query)
    hits = payload.get("hits") if isinstance(payload, dict) else None
    model["returned"] = len(hits) if isinstance(hits, list) else 0
    model["has_more"] = bool(payload.get("hasMore")) if isinstance(payload, dict) else False
    model["offset"] = max(0, offset)
    return model


def parse_byte_range(value: str, size: int) -> tuple[int, int] | None:
    """Return an inclusive byte range suitable for a native HTML media player."""
    if not value or size <= 0:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip(), flags=re.IGNORECASE)
    if not match:
        return None
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return None
    if not start_text:
        length = min(int(end_text), size)
        return size - length, size - 1
    start = int(start_text)
    if start >= size:
        return None
    end = min(int(end_text), size - 1) if end_text else size - 1
    if end < start:
        return None
    return start, end


def cache_preview_media(
    context: BrowserContext,
    request_data: dict[str, object],
    cache_directory: Path,
    token: str,
) -> dict[str, object]:
    remote_url = safe_preview_url(request_data.get("url"))
    if not remote_url:
        raise ValueError("The preview URL was missing or unsafe.")
    kind = str(request_data.get("kind") or "audio")
    if kind not in MEDIA_TYPES:
        raise ValueError("The preview type is not supported.")

    response = context.request.get(
        remote_url,
        headers={
            "Accept": "audio/*, video/*, */*;q=0.8",
            "Origin": f"{urlsplit(remote_url).scheme}://{urlsplit(remote_url).hostname}",
            "Referer": RVREMIX_URL if urlsplit(remote_url).hostname == "rvremix.com" else BASE_URL,
        },
        timeout=60_000,
    )
    provider_name = "RVRemix" if urlsplit(remote_url).hostname == "rvremix.com" else "DJPoolRecords"
    if not response.ok:
        raise RuntimeError(f"{provider_name} returned HTTP {response.status} while caching the preview.")
    body = response.body()
    if not body:
        raise RuntimeError(f"{provider_name} returned an empty preview.")

    response_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if urlsplit(remote_url).hostname == "rvremix.com" and not response_type.startswith("audio/"):
        raise RuntimeError(f"RVRemix returned non-audio preview content ({response_type or 'unknown'}).")
    requested_type = str(request_data.get("mime_type") or "").split(";", 1)[0].strip().lower()
    mime_type = response_type if response_type.startswith(("audio/", "video/")) else requested_type
    if not mime_type.startswith(("audio/", "video/")):
        mime_type = MEDIA_TYPES[kind]

    cache_directory.mkdir(parents=True, exist_ok=True)
    destination = cache_directory / token
    temporary = cache_directory / f".{token}.tmp"
    temporary.write_bytes(body)
    temporary.replace(destination)
    return {
        "path": destination,
        "mime_type": mime_type,
        "kind": "video" if mime_type.startswith("video/") else "audio",
        "source_url": remote_url,
        "size": len(body),
    }


def serve_cached_media(route: Route, media_cache: dict[str, dict[str, object]]) -> None:
    token = Path(urlsplit(route.request.url).path).name
    record = media_cache.get(token)
    path = record.get("path") if record else None
    if not isinstance(path, Path) or not path.is_file():
        route.fulfill(status=404, body="Preview not found")
        return

    size = path.stat().st_size
    requested_range = route.request.headers.get("range", "")
    byte_range = parse_byte_range(requested_range, size)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Type": str(record.get("mime_type") or "application/octet-stream"),
    }
    if requested_range and byte_range is None:
        headers["Content-Range"] = f"bytes */{size}"
        route.fulfill(status=416, headers=headers, body=b"")
        return
    if byte_range is not None:
        start, end = byte_range
        with path.open("rb") as handle:
            handle.seek(start)
            body = handle.read(end - start + 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(len(body))
        route.fulfill(status=206, headers=headers, body=body)
        return
    body = path.read_bytes()
    headers["Content-Length"] = str(len(body))
    route.fulfill(status=200, headers=headers, body=body)


def run() -> int:
    args = parse_args()
    initial_query = default_query(
        resolve_query(
            args.input_value,
            literal=args.literal_query,
            from_tags=args.query_from_tags,
        )
    )
    if args.print_query:
        print(initial_query)

    downloads_dir = Path(args.downloads_dir).expanduser().resolve()
    downloads_dir.mkdir(parents=True, exist_ok=True)
    imported = import_initial_state()
    if imported:
        print(f"Imported a one-time copy of the existing authenticated session into {STATE_PATH.name}.")

    context_options: dict[str, object] = {
        "accept_downloads": True,
        "viewport": {"width": 1280, "height": 820},
    }
    if STATE_PATH.exists():
        context_options["storage_state"] = str(STATE_PATH)

    pending_refreshes: list[str] = []
    pending_djpool_searches: dict[str, dict[str, object]] = {}
    pending_previews: list[dict[str, object]] = []
    pending_cached_downloads: list[dict[str, str]] = []
    pending_remote_downloads: list[dict[str, object]] = []
    pending_notifications: list[dict[str, object]] = []
    pending_rvremix_searches: dict[str, Future[dict[str, object]]] = {}
    media_cache: dict[str, dict[str, object]] = {}
    cached_media_by_url: dict[str, str] = {}
    cached_media_by_request: dict[str, str] = {}
    staging_dir = Path(tempfile.mkdtemp(prefix="djmetasearch_dl_"))
    preview_cache_dir = staging_dir / "previews"
    rvremix = RVRemixClient()
    rvremix_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rvremix-search")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=False,
                downloads_path=str(staging_dir),
                args=["--window-size=1280,900"],
            )
            context = browser.new_context(**context_options)
            context.route("**/wp-admin/admin-ajax.php*", rewrite_remote_headers)
            app_page = context.new_page()

            def serve_app(route: Route) -> None:
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=UI_PATH.read_text(encoding="utf-8"),
                    headers={
                        "Cache-Control": "no-store",
                        "Content-Security-Policy": (
                            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                            "connect-src 'self'; media-src 'self' blob:; img-src 'self' data:; form-action 'none'"
                        ),
                    },
                )

            app_page.route(f"{APP_URL}*", serve_app)
            app_page.route(
                f"{MEDIA_URL_PREFIX}*",
                lambda route: serve_cached_media(route, media_cache),
            )

            def queue_djpool_search(_source: object, query: str) -> str:
                request_id = uuid.uuid4().hex
                pending_djpool_searches[request_id] = {
                    "query": str(query or ""),
                    "offset": 0,
                    "model": None,
                }
                return request_id

            def queue_rvremix_search(_source: object, query: str) -> str:
                request_id = uuid.uuid4().hex
                pending_rvremix_searches[request_id] = rvremix_executor.submit(
                    rvremix.search, str(query or "")
                )
                return request_id

            def merge_models(_source: object, raw: str) -> dict[str, object]:
                request = json.loads(raw)
                if not isinstance(request, dict):
                    raise ValueError("Merged search data was invalid.")
                return merge_result_models(
                    request.get("models"),
                    later_title_term=str(request.get("later_title_term") or ""),
                )

            def queue_refresh(_source: object, query: str) -> bool:
                pending_refreshes.append(str(query or ""))
                return True

            def queue_preview(_source: object, raw: str) -> str:
                request_data = json.loads(raw)
                if not isinstance(request_data, dict):
                    raise ValueError("Preview request was invalid.")
                request_id = uuid.uuid4().hex
                request_data["request_id"] = request_id
                pending_previews.append(request_data)
                return request_id

            def queue_cached_download(_source: object, raw: str) -> bool:
                request_data = json.loads(raw)
                if not isinstance(request_data, dict):
                    raise ValueError("Cached download request was invalid.")
                request_id = str(request_data.get("request_id") or "")
                if not request_id:
                    raise ValueError("Cached download request did not identify a preview.")
                pending_cached_downloads.append(
                    {
                        "request_id": request_id,
                        "filename": str(request_data.get("filename") or "download"),
                    }
                )
                return True

            def queue_remote_download(_source: object, raw: str) -> bool:
                request_data = json.loads(raw)
                if not isinstance(request_data, dict):
                    raise ValueError("Remote download request was invalid.")
                remote_url = safe_preview_url(request_data.get("url"))
                if urlsplit(remote_url).hostname != "rvremix.com":
                    raise ValueError("Remote download URL was missing or unsafe.")
                request_data["url"] = remote_url
                request_data["kind"] = "audio"
                pending_remote_downloads.append(request_data)
                return True

            app_page.expose_binding("requestDJPoolSearch", queue_djpool_search)
            app_page.expose_binding("requestRVRemixSearch", queue_rvremix_search)
            app_page.expose_binding("mergeResultModels", merge_models)
            app_page.expose_binding("requestBootstrap", queue_refresh)
            app_page.expose_binding("requestPreview", queue_preview)
            app_page.expose_binding("requestCachedDownload", queue_cached_download)
            app_page.expose_binding("requestRemoteDownload", queue_remote_download)

            def handle_download(download: object) -> None:
                try:
                    message, warning = complete_download(download, downloads_dir)
                    if warning:
                        print(message, file=sys.stderr)
                    else:
                        print(message)
                    pending_notifications.append({"message": message, "warning": warning})
                except Exception as exc:
                    message = f"Download failed: {exc}"
                    print(message, file=sys.stderr)
                    pending_notifications.append({"message": message, "warning": True})

            app_page.on("download", handle_download)
            app_page.goto(APP_URL, wait_until="load")
            djpool_template = load_cache(CACHE_PATH)
            if not evaluate_open_page(
                app_page,
                "([cache, query]) => window.djpool.start(cache, query)",
                [djpool_template, initial_query],
            ):
                browser.close()
                return 0

            while not app_page.is_closed():
                if pending_djpool_searches:
                    request_id = next(iter(pending_djpool_searches))
                    job = pending_djpool_searches.pop(request_id)
                    query = str(job["query"])
                    offset = int(job["offset"])
                    previous_model = job.get("model")
                    try:
                        page_model = search_djpoolrecords(
                            context, djpool_template, query, offset=offset
                        )
                        raw_returned = int(page_model.get("returned") or 0)
                        has_more = bool(page_model.get("has_more"))
                        visible_page = {
                            "count": int(page_model.get("count") or 0),
                            "results": page_model.get("results") or [],
                        }
                        if isinstance(previous_model, dict):
                            model = merge_result_models([previous_model, visible_page])
                            added = len(model["results"]) - len(previous_model["results"])
                        else:
                            model = visible_page
                            added = len(model["results"])
                        done = (
                            not has_more
                            or raw_returned == 0
                            or (offset > 0 and added == 0)
                            or offset >= 9_950
                        )
                        callback = (
                            "djpoolSearchSucceeded"
                            if previous_model is None
                            else "djpoolSearchUpdated"
                        )
                        if not evaluate_open_page(
                            app_page,
                            f"([requestId, model, done]) => window.djpool.{callback}(requestId, model, done)",
                            [request_id, model, done],
                        ):
                            break
                        if not done:
                            job["offset"] = offset + 50
                            job["model"] = model
                            pending_djpool_searches[request_id] = job
                    except Exception as exc:
                        callback = (
                            "djpoolSearchFailed"
                            if previous_model is None
                            else "djpoolSearchPaginationFailed"
                        )
                        if not evaluate_open_page(
                            app_page,
                            f"([requestId, message]) => window.djpool.{callback}(requestId, message)",
                            [request_id, str(exc)],
                        ):
                            break
                closed_during_callback = False
                completed_rvremix = [
                    request_id
                    for request_id, future in pending_rvremix_searches.items()
                    if future.done()
                ]
                for request_id in completed_rvremix:
                    future = pending_rvremix_searches.pop(request_id)
                    try:
                        model = future.result()
                        if not evaluate_open_page(
                            app_page,
                            "([requestId, model]) => window.djpool.rvremixSearchSucceeded(requestId, model)",
                            [request_id, model],
                        ):
                            closed_during_callback = True
                            break
                    except Exception as exc:
                        if not evaluate_open_page(
                            app_page,
                            "([requestId, message]) => window.djpool.rvremixSearchFailed(requestId, message)",
                            [request_id, str(exc)],
                        ):
                            closed_during_callback = True
                            break
                if closed_during_callback:
                    break
                if pending_notifications:
                    notification = pending_notifications.pop(0)
                    if not evaluate_open_page(
                        app_page,
                        "([message, warning]) => window.djpool.downloadFinished(message, warning)",
                        [notification["message"], notification["warning"]],
                    ):
                        break
                if pending_cached_downloads:
                    cached_download = pending_cached_downloads.pop(0)
                    try:
                        token = cached_media_by_request.get(cached_download["request_id"], "")
                        record = media_cache.get(token)
                        if not record:
                            raise RuntimeError("The cached preview is no longer available.")
                        message, warning = complete_cached_preview_download(
                            record,
                            cached_download["filename"],
                            downloads_dir,
                        )
                        if warning:
                            print(message, file=sys.stderr)
                        else:
                            print(message)
                        pending_notifications.append({"message": message, "warning": warning})
                    except Exception as exc:
                        message = f"Download failed: {exc}"
                        print(message, file=sys.stderr)
                        pending_notifications.append({"message": message, "warning": True})
                if pending_remote_downloads:
                    remote_download = pending_remote_downloads.pop(0)
                    remote_url = str(remote_download["url"])
                    try:
                        token = cached_media_by_url.get(remote_url, "")
                        if not token or token not in media_cache:
                            token = uuid.uuid4().hex
                            media_cache[token] = cache_preview_media(
                                context,
                                remote_download,
                                preview_cache_dir,
                                token,
                            )
                            cached_media_by_url[remote_url] = token
                        message, warning = complete_cached_preview_download(
                            media_cache[token],
                            str(remote_download.get("filename") or "download.mp3"),
                            downloads_dir,
                        )
                        if warning:
                            print(message, file=sys.stderr)
                        else:
                            print(message)
                        pending_notifications.append({"message": message, "warning": warning})
                    except Exception as exc:
                        message = f"Download failed: {exc}"
                        print(message, file=sys.stderr)
                        pending_notifications.append({"message": message, "warning": True})
                if pending_previews:
                    preview_request = pending_previews.pop(0)
                    request_id = str(preview_request.get("request_id") or "")
                    remote_url = safe_preview_url(preview_request.get("url"))
                    try:
                        if not remote_url:
                            raise ValueError("The preview URL was missing or unsafe.")
                        token = cached_media_by_url.get(remote_url, "")
                        if not token or token not in media_cache:
                            token = uuid.uuid4().hex
                            media_cache[token] = cache_preview_media(
                                context,
                                preview_request,
                                preview_cache_dir,
                                token,
                            )
                            cached_media_by_url[remote_url] = token
                        record = media_cache[token]
                        cached_media_by_request[request_id] = token
                        local_url = f"{MEDIA_URL_PREFIX}{token}"
                        if not evaluate_open_page(
                            app_page,
                            "([requestId, url, kind]) => window.djpool.previewReady(requestId, url, kind)",
                            [request_id, local_url, record["kind"]],
                        ):
                            break
                    except Exception as exc:
                        message = f"Could not cache preview: {exc}"
                        print(message, file=sys.stderr)
                        if not evaluate_open_page(
                            app_page,
                            "([requestId, message]) => window.djpool.previewFailed(requestId, message)",
                            [request_id, message],
                        ):
                            break
                if pending_refreshes:
                    query = pending_refreshes[-1]
                    pending_refreshes.clear()
                    try:
                        refreshed = capture_bootstrap(context, app_page, query)
                        djpool_template = refreshed
                        if not evaluate_open_page(
                            app_page,
                            "([cache, query]) => window.djpool.bootstrapSucceeded(cache, query)",
                            [refreshed, query],
                        ):
                            break
                    except Exception as exc:
                        message = f"Could not refresh the DJPoolRecords session: {exc}"
                        print(message, file=sys.stderr)
                        if not evaluate_open_page(
                            app_page,
                            "message => window.djpool.bootstrapFailed(message)",
                            message,
                        ):
                            break
                try:
                    app_page.wait_for_timeout(250)
                except Exception:
                    break

            try:
                if has_wordpress_session(context):
                    context.storage_state(path=str(STATE_PATH))
            except Exception:
                pass
            browser.close()
    finally:
        rvremix_executor.shutdown(wait=False, cancel_futures=True)
        shutil.rmtree(staging_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
