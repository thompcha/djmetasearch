import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from djmetasearch.results import merge_result_models, parse_search_payload


ROOT = Path(__file__).parents[1]
APP_URL = "https://djpoolrecords.com/__djmetasearch__/"
SEARCH_URL = "https://djpoolrecords.com/wp-json/dpr-search/v1/files"
MEDIA_URL = "https://djpoolrecords.com/__djmetasearch_media__/fixture-preview"
UI = (ROOT / "ui" / "app.html").read_text(encoding="utf-8")
TEMPLATE = {"schema": 2, "url": SEARCH_URL, "nonce": "fixture-nonce"}


def rest_payload(*names: str) -> dict[str, object]:
    return {
        "hits": [
            {
                "name": name,
                "ext": "mp3",
                "size": "10 MB" if index == 0 else "",
                "mime": "audio/mpeg",
                "stream": (
                    "https://ucfixture.dl.dropboxusercontent.com/cd/0/get/file?token=fixture"
                    if index == 0 else ""
                ),
                "download": (
                    f"https://djpoolrecords.com/wp-admin/admin-ajax.php?action=download&id={index}"
                    if index < 2 else ""
                ),
            }
            for index, name in enumerate(names)
        ],
        "estimatedTotalHits": len(names),
        "hasMore": False,
        "processingTimeMs": 1,
    }


PAYLOAD = rest_payload("Artist - Song", "Artist - Video", "Metadata only")


def test_cached_launch_searches_without_homepage_and_renders_results() -> None:
    seen: list[str] = []
    refreshes: list[str] = []
    previews: list[dict[str, object]] = []
    cached_downloads: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.on("request", lambda request: seen.append(request.url))
        page.route(
            f"{APP_URL}*",
            lambda route: route.fulfill(status=200, content_type="text/html", body=UI),
        )
        page.route(
            f"{MEDIA_URL}*",
            lambda route: route.fulfill(status=200, content_type="audio/mpeg", body=b"fixture-audio"),
        )
        page.expose_binding(
            "parseResultPayload",
            lambda _source, raw: parse_search_payload(json.loads(raw)),
        )
        page.expose_binding("requestDJPoolSearch", lambda _source, query: parse_search_payload(PAYLOAD))
        page.expose_binding("requestRVRemixSearch", lambda _source, query: {"count": 0, "results": []})
        page.expose_binding(
            "mergeResultModels",
            lambda _source, raw: merge_result_models(
                json.loads(raw)["models"],
                later_title_term=json.loads(raw)["later_title_term"],
            ),
        )
        page.expose_binding("requestBootstrap", lambda _source, query: refreshes.append(query) or True)
        page.expose_binding(
            "requestPreview",
            lambda _source, raw: previews.append(json.loads(raw)) or "preview-request-1",
        )
        page.expose_binding(
            "requestCachedDownload",
            lambda _source, raw: cached_downloads.append(json.loads(raw)) or True,
        )
        page.goto(APP_URL)
        page.evaluate("([cache, query]) => window.djpool.start(cache, query)", [TEMPLATE, "Artist intro"])
        page.wait_for_selector(".result", timeout=5_000)

        assert page.locator(".result").count() == 3
        assert page.locator("#count").inner_text() == "3 results · 3 DJPoolRecords"
        assert page.locator(".provider", has_text="DJPoolRecords").count() == 3
        assert page.locator("#status").inner_text() == ""
        page.wait_for_function("() => getComputedStyle(document.querySelector('#status')).visibility === 'hidden'")
        assert not page.locator("#status").is_visible()
        first_y = page.locator(".result").first.bounding_box()["y"]
        page.evaluate(
            "() => window.djpool.downloadFinished('Downloaded and cleaned: Artist - Title.mp3', false)"
        )
        assert page.locator("#status").inner_text() == "Downloaded and cleaned: Artist - Title.mp3"
        assert page.locator(".result").first.bounding_box()["y"] == first_y
        page.locator("#query").fill("Artist intro")
        page.locator(".variant", has_text="Clean").click()
        page.wait_for_function("() => document.querySelector('#query').value === 'Artist clean'")
        page.wait_for_function("() => !document.querySelector('#search-button').disabled")
        page.locator(".variant", has_text="QH").click()
        page.wait_for_function("() => document.querySelector('#query').value === 'Artist QH'")
        page.wait_for_function("() => !document.querySelector('#search-button').disabled")
        page.locator(".variant", has_text="Segue").click()
        page.wait_for_function("() => document.querySelector('#query').value === 'Artist segue'")
        page.wait_for_function("() => !document.querySelector('#search-button').disabled")
        page.locator(".variant", has_text="Short").click()
        page.wait_for_function("() => document.querySelector('#query').value === 'Artist short'")
        page.wait_for_function("() => !document.querySelector('#search-button').disabled")
        page.locator("#clear-modifier").click()
        page.wait_for_function("() => document.querySelector('#query').value === 'Artist'")
        page.wait_for_function("() => !document.querySelector('#search-button').disabled")
        assert page.locator(".result .action.download").count() == 2
        first_result = page.locator(".result.previewable").first
        preview_icon = first_result.locator(".preview-action")
        assert "AUDIO" not in first_result.inner_text()
        assert first_result.locator(".result-size").inner_text() == "10 MB"
        name_box = first_result.locator(".result-name").bounding_box()
        size_box = first_result.locator(".result-size").bounding_box()
        assert name_box is not None and size_box is not None
        assert size_box["x"] > name_box["x"] + name_box["width"]
        assert preview_icon.inner_text() == "▶"
        assert preview_icon.evaluate("element => getComputedStyle(element).opacity") == "0"
        preview_box = preview_icon.bounding_box()
        download_box = first_result.locator(".download").bounding_box()
        assert preview_box is not None and download_box is not None
        assert preview_box["x"] < size_box["x"] < download_box["x"]
        tile_box = first_result.bounding_box()
        assert tile_box is not None and tile_box["height"] == 58
        first_result.hover()
        page.wait_for_function(
            "element => Number(getComputedStyle(element).opacity) === 1",
            arg=preview_icon.element_handle(),
        )
        first_result.locator(".download").evaluate(
            "element => element.addEventListener('click', event => event.preventDefault(), { once: true })"
        )
        first_result.locator(".download").click()
        assert previews == []
        first_result.click(position={"x": 12, "y": 12})
        page.wait_for_selector("#player.visible")
        page.wait_for_function("() => document.querySelector('.preview-loading')?.textContent.includes('Caching')")
        caching_tile_box = first_result.bounding_box()
        assert caching_tile_box is not None
        assert caching_tile_box["y"] == tile_box["y"]
        assert previews[0]["url"].startswith("https://ucfixture.dl.dropboxusercontent.com/")
        player_download = page.locator("#player-download")
        assert player_download.is_hidden()
        page.evaluate(
            "([id, url]) => window.djpool.previewReady(id, url, 'audio')",
            ["preview-request-1", MEDIA_URL],
        )
        page.wait_for_selector("#player audio[controls]")
        assert page.locator("#player audio").get_attribute("src") == MEDIA_URL
        assert "nodownload" in (page.locator("#player audio").get_attribute("controlslist") or "")
        assert player_download.is_visible()
        assert player_download.evaluate("element => element.tagName") == "BUTTON"
        assert player_download.get_attribute("href") is None
        player_download.click()
        assert cached_downloads == [{
            "request_id": "preview-request-1",
            "filename": first_result.locator(".download").get_attribute("download"),
        }]
        assert page.locator("#status").inner_text().startswith("Saving cached preview ")
        assert player_download.is_disabled()
        page.evaluate("() => window.djpool.downloadFinished('Downloaded: Artist - Title.mp3', false)")
        assert not player_download.is_disabled()
        page.wait_for_function(
            "() => Math.abs(document.querySelector('#player').getBoundingClientRect().bottom - window.innerHeight) < 1"
        )
        player_box = page.locator("#player").bounding_box()
        viewport_height = page.evaluate("window.innerHeight")
        assert player_box is not None
        assert abs(player_box["y"] + player_box["height"] - viewport_height) < 1
        assert page.locator("body").evaluate("element => element.classList.contains('preview-open')")
        assert BASE_PAGE not in seen
        assert refreshes == []
        browser.close()


def test_failed_cached_search_requests_one_background_refresh() -> None:
    refreshes: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(f"{APP_URL}*", lambda route: route.fulfill(status=200, content_type="text/html", body=UI))
        page.expose_binding("parseResultPayload", lambda _source, raw: parse_search_payload(json.loads(raw)))
        page.expose_binding(
            "requestDJPoolSearch",
            lambda _source, query: (_ for _ in ()).throw(RuntimeError("expired")),
        )
        page.expose_binding("requestRVRemixSearch", lambda _source, query: {"count": 0, "results": []})
        page.expose_binding("requestBootstrap", lambda _source, query: refreshes.append(query) or True)
        page.goto(APP_URL)
        page.evaluate("([cache, query]) => window.djpool.start(cache, query)", [TEMPLATE, "Expired Query"])
        page.wait_for_function("() => document.querySelector('#status').textContent.includes('background')", timeout=5_000)
        assert refreshes == ["Expired Query"]
        browser.close()


def test_combines_and_labels_rvremix_results() -> None:
    remote_downloads: list[dict[str, object]] = []
    rv_model = {
        "count": 1,
        "results": [{
            "id": "rv-1",
            "name": "Artist - RV Audio",
            "filename": "Artist - RV Audio.mp3",
            "size": "8 MB",
            "kind": "audio",
            "mime_type": "audio/mpeg",
            "preview_url": "https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-stream&id=rv-1",
            "download_url": "https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-stream&id=rv-1",
            "provider": "RVRemix",
        }],
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(f"{APP_URL}*", lambda route: route.fulfill(status=200, content_type="text/html", body=UI))
        page.expose_binding("parseResultPayload", lambda _source, raw: parse_search_payload(json.loads(raw)))
        page.expose_binding("requestDJPoolSearch", lambda _source, query: parse_search_payload(PAYLOAD))
        page.expose_binding("requestRVRemixSearch", lambda _source, query: rv_model)
        page.expose_binding(
            "mergeResultModels",
            lambda _source, raw: merge_result_models(
                json.loads(raw)["models"],
                later_title_term=json.loads(raw)["later_title_term"],
            ),
        )
        page.expose_binding("requestBootstrap", lambda _source, query: True)
        page.expose_binding(
            "requestRemoteDownload",
            lambda _source, raw: remote_downloads.append(json.loads(raw)) or True,
        )
        page.goto(APP_URL)
        page.evaluate("([cache, query]) => window.djpool.start(cache, query)", [TEMPLATE, "Artist"])
        page.wait_for_selector(".provider.rvremix")

        assert page.locator(".result").count() == 4
        assert page.locator("#count").inner_text() == "4 results · 3 DJPoolRecords · 1 RVRemix"
        rv_row = page.locator(".result", has=page.locator(".provider.rvremix"))
        assert rv_row.locator(".result-name").inner_text() == "Artist - RV Audio"
        rv_download = rv_row.locator(".download")
        assert rv_download.evaluate("element => element.tagName") == "BUTTON"
        assert rv_download.get_attribute("href") is None
        rv_download.click()
        assert remote_downloads == [{
            "id": "rv-1",
            "filename": "Artist - RV Audio.mp3",
            "mime_type": "audio/mpeg",
            "url": "https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-stream&id=rv-1",
        }]
        browser.close()


def test_renders_djpoolrecords_before_delayed_rvremix_results() -> None:
    rv_model = {
        "count": 1,
        "results": [{
            "id": "rv-delayed",
            "name": "Artist - Delayed RV Audio",
            "filename": "Artist - Delayed RV Audio.mp3",
            "size": "7 MB",
            "kind": "audio",
            "mime_type": "audio/mpeg",
            "preview_url": "https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-stream&id=delayed",
            "download_url": "https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-stream&id=delayed",
            "provider": "RVRemix",
        }],
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(f"{APP_URL}*", lambda route: route.fulfill(status=200, content_type="text/html", body=UI))
        page.expose_binding("parseResultPayload", lambda _source, raw: parse_search_payload(json.loads(raw)))
        page.expose_binding("requestDJPoolSearch", lambda _source, query: parse_search_payload(PAYLOAD))
        page.expose_binding("requestRVRemixSearch", lambda _source, query: "rv-request-delayed")
        page.expose_binding(
            "mergeResultModels",
            lambda _source, raw: merge_result_models(
                json.loads(raw)["models"],
                later_title_term=json.loads(raw)["later_title_term"],
            ),
        )
        page.expose_binding("requestBootstrap", lambda _source, query: True)
        page.goto(APP_URL)
        page.evaluate("([cache, query]) => window.djpool.start(cache, query)", [TEMPLATE, "Artist"])

        page.wait_for_function("() => document.querySelectorAll('.result').length === 3")
        assert page.locator("#count").inner_text() == "3 results · 3 DJPoolRecords"
        assert page.locator("#status").inner_text() == "DJPoolRecords results ready; loading RVRemix…"

        page.evaluate(
            "model => window.djpool.rvremixSearchSucceeded('rv-request-delayed', model)",
            rv_model,
        )
        page.wait_for_selector(".provider.rvremix")
        assert page.locator("#count").inner_text() == "4 results · 3 DJPoolRecords · 1 RVRemix"
        assert page.locator("#status").inner_text() == ""
        browser.close()


def test_crate_search_arrives_asynchronously_and_resolves_only_on_action() -> None:
    crate_model = {
        "count": 1,
        "results": [{
            "id": "crate-6080796180102",
            "provider_item_id": "6080796180102",
            "name": "Artist - Crate Audio 108.mp3",
            "display_name": "Artist - Crate Audio",
            "filename": "Artist - Crate Audio 108.mp3",
            "bpm": 108,
            "size": "5.5 MB",
            "size_bytes": 5_815_654,
            "kind": "audio",
            "mime_type": "audio/mpeg",
            "preview_url": "",
            "download_url": "",
            "resolvable": True,
            "provider": "DJFolders",
        }],
    }
    stream_url = "https://pod.djpanaflex.com/crate-search/?action=stream&id=6080796180102&key=fixture"
    download_url = "https://pod.djpanaflex.com/crate-search/?action=download&id=6080796180102&key=fixture"
    resolves: list[str] = []
    remote_downloads: list[dict[str, object]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(f"{APP_URL}*", lambda route: route.fulfill(status=200, content_type="text/html", body=UI))
        page.route(
            "https://pod.djpanaflex.com/crate-search/**",
            lambda route: route.fulfill(status=200, content_type="audio/mpeg", body=b"fixture-audio"),
        )
        page.expose_binding("requestDJPoolSearch", lambda _source, query: parse_search_payload(PAYLOAD))
        page.expose_binding("requestRVRemixSearch", lambda _source, query: {"count": 0, "results": []})
        page.expose_binding("requestCrateSearch", lambda _source, query: "crate-request")
        page.expose_binding(
            "requestCrateResolve",
            lambda _source, track_id: resolves.append(track_id) or {
                "preview_url": stream_url,
                "download_url": download_url,
                "mime_type": "audio/mpeg",
                "direct_stream": True,
            },
        )
        page.expose_binding(
            "requestRemoteDownload",
            lambda _source, raw: remote_downloads.append(json.loads(raw)) or True,
        )
        page.expose_binding(
            "mergeResultModels",
            lambda _source, raw: merge_result_models(
                json.loads(raw)["models"],
                later_title_term=json.loads(raw)["later_title_term"],
            ),
        )
        page.expose_binding("requestBootstrap", lambda _source, query: True)
        page.goto(APP_URL)
        page.evaluate(
            "([cache, query]) => window.djpool.start(cache, query, { djfolders: true })",
            [TEMPLATE, "Artist"],
        )

        page.wait_for_function("() => document.querySelectorAll('.result').length === 3")
        assert "DJFolders" in page.locator("#status").inner_text()
        assert resolves == []

        page.evaluate(
            "model => window.djpool.crateSearchSucceeded('crate-request', model)",
            crate_model,
        )
        page.wait_for_selector(".provider.djfolders")
        assert page.locator("#count").inner_text() == (
            "4 results · 3 DJPoolRecords · 1 DJFolders"
        )
        crate_row = page.locator(".result", has=page.locator(".provider.djfolders"))
        assert crate_row.locator(".result-bpm").inner_text() == "108"
        assert resolves == []

        crate_row.click(position={"x": 12, "y": 12})
        page.wait_for_selector("#player audio")
        assert resolves == ["6080796180102"]
        assert page.locator("#player audio").get_attribute("src") == stream_url
        page.locator("#player-download").click()
        assert remote_downloads == [{
            "id": "crate-6080796180102",
            "filename": "Artist - Crate Audio 108.mp3",
            "mime_type": "audio/mpeg",
            "url": download_url,
        }]
        browser.close()


def test_missing_djfolders_key_is_reported_instead_of_silently_omitted() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(f"{APP_URL}*", lambda route: route.fulfill(status=200, content_type="text/html", body=UI))
        page.expose_binding("requestDJPoolSearch", lambda _source, query: parse_search_payload(PAYLOAD))
        page.expose_binding("requestRVRemixSearch", lambda _source, query: {"count": 0, "results": []})
        page.expose_binding(
            "mergeResultModels",
            lambda _source, raw: merge_result_models(
                json.loads(raw)["models"],
                later_title_term=json.loads(raw)["later_title_term"],
            ),
        )
        page.expose_binding("requestBootstrap", lambda _source, query: True)
        page.goto(APP_URL)
        page.evaluate(
            "([cache, query, options]) => window.djpool.start(cache, query, options)",
            [TEMPLATE, "de la soul", {
                "djfolders": False,
                "djfoldersReason": "API key not configured",
            }],
        )
        page.wait_for_function(
            "() => document.querySelector('#status').textContent.includes('DJFolders unavailable')"
        )
        assert page.locator("#status").inner_text() == (
            "DJFolders unavailable: API key not configured"
        )
        browser.close()


def test_djpool_later_pages_merge_asynchronously_and_finish() -> None:
    initial = parse_search_payload(rest_payload("Artist - First", "Artist - Second"))
    accumulated = parse_search_payload(
        rest_payload("Artist - First", "Artist - Second", "Artist - Third")
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(f"{APP_URL}*", lambda route: route.fulfill(status=200, content_type="text/html", body=UI))
        page.expose_binding("requestDJPoolSearch", lambda _source, query: "dj-progressive")
        page.expose_binding("requestRVRemixSearch", lambda _source, query: {"count": 0, "results": []})
        page.expose_binding(
            "mergeResultModels",
            lambda _source, raw: merge_result_models(
                json.loads(raw)["models"],
                later_title_term=json.loads(raw)["later_title_term"],
            ),
        )
        page.expose_binding("requestBootstrap", lambda _source, query: True)
        page.goto(APP_URL)
        page.evaluate("([cache, query]) => window.djpool.start(cache, query)", [TEMPLATE, "Artist"])

        page.evaluate(
            "model => window.djpool.djpoolSearchSucceeded('dj-progressive', model, false)",
            initial,
        )
        page.wait_for_function("() => document.querySelectorAll('.result').length === 2")
        assert page.locator("#status").inner_text() == "Loading more DJPoolRecords…"

        page.evaluate(
            "model => window.djpool.djpoolSearchUpdated('dj-progressive', model, true)",
            accumulated,
        )
        page.wait_for_function("() => document.querySelectorAll('.result').length === 3")
        assert page.locator("#count").inner_text() == "3 results · 3 DJPoolRecords"
        assert page.locator("#status").inner_text() == ""
        browser.close()


def test_clear_reuses_inflight_bare_requests_and_accepts_late_rvremix_results() -> None:
    djpool_queries: list[str] = []
    rvremix_queries: list[str] = []
    rv_model = {
        "count": 1,
        "results": [{
            "id": "rv-bare",
            "name": "Artist - Bare Match",
            "filename": "Artist - Bare Match.mp3",
            "size": "6 MB",
            "kind": "audio",
            "mime_type": "audio/mpeg",
            "preview_url": "https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-stream&id=bare",
            "download_url": "https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-stream&id=bare",
            "provider": "RVRemix",
        }],
    }

    def serve_search(_source, query: str) -> dict[str, object]:
        djpool_queries.append(query)
        return parse_search_payload(PAYLOAD)

    def queue_rvremix(_source, query: str) -> str:
        rvremix_queries.append(query)
        return f"rv-{query}"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(f"{APP_URL}*", lambda route: route.fulfill(status=200, content_type="text/html", body=UI))
        page.expose_binding("parseResultPayload", lambda _source, raw: parse_search_payload(json.loads(raw)))
        page.expose_binding("requestDJPoolSearch", serve_search)
        page.expose_binding("requestRVRemixSearch", queue_rvremix)
        page.expose_binding(
            "mergeResultModels",
            lambda _source, raw: merge_result_models(
                json.loads(raw)["models"],
                later_title_term=json.loads(raw)["later_title_term"],
            ),
        )
        page.expose_binding("requestBootstrap", lambda _source, query: True)
        page.goto(APP_URL)
        page.evaluate("([cache, query]) => window.djpool.start(cache, query)", [TEMPLATE, "Artist intro"])
        page.wait_for_function("() => document.querySelector('#query').value === 'Artist intro'")
        page.wait_for_function(
            "() => document.querySelector('#status').textContent.includes('loading RVRemix')"
        )
        assert set(rvremix_queries) == {"Artist intro", "Artist"}

        page.locator("#clear-modifier").click()
        page.wait_for_function("() => document.querySelector('#query').value === 'Artist'")
        page.wait_for_function("() => document.querySelectorAll('.result').length === 3")
        assert rvremix_queries.count("Artist") == 1
        assert djpool_queries.count("Artist") == 1
        assert page.locator("#status").inner_text() == "DJPoolRecords results ready; loading RVRemix…"

        page.evaluate(
            "model => window.djpool.rvremixSearchSucceeded('rv-Artist', model)",
            rv_model,
        )
        page.wait_for_selector(".provider.rvremix")
        assert page.locator(".result-name", has_text="Artist - Bare Match").count() == 1
        assert page.locator("#count").inner_text() == "4 results · 3 DJPoolRecords · 1 RVRemix"
        browser.close()


def test_intro_search_merges_exact_and_base_queries_without_duplicates() -> None:
    queries: list[str] = []
    refreshes: list[str] = []
    exact_payload = rest_payload("Blood - Spinning Wheel (Intro)")
    base_payload = rest_payload(
        "Blood - Spinning Wheel (Intro)",
        "Blood Sweat And Tears - Spinning Wheel (Intro) 97",
        "Blood Sweat And Tears - Spinning Wheel 97",
    )

    def serve_search(_source, query: str) -> dict[str, object]:
        queries.append(query)
        payload = exact_payload if query.lower().endswith(" intro") else base_payload
        return parse_search_payload(payload)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(f"{APP_URL}*", lambda route: route.fulfill(status=200, content_type="text/html", body=UI))
        page.expose_binding("parseResultPayload", lambda _source, raw: parse_search_payload(json.loads(raw)))
        page.expose_binding("requestDJPoolSearch", serve_search)
        page.expose_binding("requestRVRemixSearch", lambda _source, query: {"count": 0, "results": []})
        page.expose_binding(
            "mergeResultModels",
            lambda _source, raw: merge_result_models(
                json.loads(raw)["models"],
                later_title_term=json.loads(raw)["later_title_term"],
            ),
        )
        page.expose_binding("requestBootstrap", lambda _source, query: refreshes.append(query) or True)
        page.goto(APP_URL)
        original_query = "Blood - Spinning Wheel intro"
        page.evaluate("([cache, query]) => window.djpool.start(cache, query)", [TEMPLATE, original_query])
        page.wait_for_selector(".result")

        assert queries == [original_query, "Blood - Spinning Wheel"]
        assert page.locator("#query").input_value() == original_query
        assert page.locator(".result").count() == 2
        assert page.locator(".result-name").all_inner_texts() == [
            "Blood - Spinning Wheel (Intro)",
            "Blood Sweat And Tears - Spinning Wheel (Intro)",
        ]
        assert page.locator(".result-bpm").all_inner_texts() == ["", "97"]
        assert page.locator("#status").inner_text() == ""
        assert refreshes == []
        browser.close()


def test_numeric_columns_sort_both_directions_with_blanks_last() -> None:
    payload = {
        "hits": [
            {"name": "Artist - High 128", "ext": "mp3", "size": "10 MB", "mime": "audio/mpeg"},
            {"name": "Artist - Missing BPM", "ext": "mp3", "size": "2 MB", "mime": "audio/mpeg"},
            {"name": "Artist - Low 90", "ext": "mp3", "size": "5 MB", "mime": "audio/mpeg"},
            {"name": "Artist - Unknown", "ext": "mp3", "size": "", "mime": "audio/mpeg"},
        ]
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(f"{APP_URL}*", lambda route: route.fulfill(status=200, content_type="text/html", body=UI))
        page.expose_binding("parseResultPayload", lambda _source, raw: parse_search_payload(json.loads(raw)))
        page.expose_binding("requestDJPoolSearch", lambda _source, query: parse_search_payload(payload))
        page.expose_binding("requestRVRemixSearch", lambda _source, query: {"count": 0, "results": []})
        page.expose_binding(
            "mergeResultModels",
            lambda _source, raw: merge_result_models(
                json.loads(raw)["models"],
                later_title_term=json.loads(raw)["later_title_term"],
            ),
        )
        page.expose_binding("requestBootstrap", lambda _source, query: True)
        page.goto(APP_URL)
        page.evaluate("([cache, query]) => window.djpool.start(cache, query)", [TEMPLATE, "Artist"])
        page.wait_for_selector(".bpm-sort")

        assert page.locator(".result-name").all_inner_texts() == [
            "Artist - High",
            "Artist - Missing BPM",
            "Artist - Low",
            "Artist - Unknown",
        ]
        assert page.locator(".result-bpm").all_inner_texts() == ["128", "", "90", ""]

        page.locator(".bpm-sort").click()
        assert page.locator(".result-name").all_inner_texts() == [
            "Artist - Low",
            "Artist - High",
            "Artist - Missing BPM",
            "Artist - Unknown",
        ]
        assert page.locator(".bpm-sort").inner_text() == "BPM ↑"

        page.locator(".bpm-sort").click()
        assert page.locator(".result-name").all_inner_texts() == [
            "Artist - High",
            "Artist - Low",
            "Artist - Missing BPM",
            "Artist - Unknown",
        ]
        assert page.locator(".bpm-sort").inner_text() == "BPM ↓"

        page.locator(".size-sort").click()
        assert page.locator(".result-name").all_inner_texts() == [
            "Artist - Missing BPM",
            "Artist - Low",
            "Artist - High",
            "Artist - Unknown",
        ]
        assert page.locator(".size-sort").inner_text() == "SIZE ↑"
        assert page.locator(".bpm-sort").inner_text() == "BPM ↕"

        page.locator(".size-sort").click()
        assert page.locator(".result-name").all_inner_texts() == [
            "Artist - High",
            "Artist - Low",
            "Artist - Missing BPM",
            "Artist - Unknown",
        ]
        assert page.locator(".size-sort").inner_text() == "SIZE ↓"
        browser.close()


BASE_PAGE = "https://djpoolrecords.com/"
