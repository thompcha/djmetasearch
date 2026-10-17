import urllib.error
import io
import json
from email.message import Message
from urllib.parse import parse_qs

import pytest

from djmetasearch.rvremix import (
    RVRemixClient,
    parse_search_payload,
    filter_rvremix_model,
    normalized_tokens,
    quoted_rvremix_query,
    rvremix_query_groups,
)


ENDPOINT = "https://rvremix.com/wp-admin/admin-ajax.php"


def response(value):
    body = io.BytesIO(json.dumps(value).encode())
    return body


def test_bootstrap_reads_current_search_widget(monkeypatch):
    client = RVRemixClient()
    html = b'<script>const endpoint="https:\\/\\/rvremix.com\\/wp-admin\\/admin-ajax.php",nonce="fresh"; const body={action:\'rvrs_search\'};</script>'
    body = io.BytesIO(html)
    body.headers = Message()
    monkeypatch.setattr(client, "_request", lambda *_: body)
    assert client.bootstrap() == {"url": ENDPOINT, "nonce": "fresh"}


def test_search_refreshes_nonce_after_unauthorized(monkeypatch):
    client = RVRemixClient()
    client.template = {"url": ENDPOINT, "nonce": "stale"}
    calls = []
    def request(url, data):
        form = parse_qs(data.decode())
        calls.append(form)
        if form["nonce"] == ["stale"]:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return response({"success": True, "data": {"items": []}})
    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(client, "bootstrap", lambda: {"url": ENDPOINT, "nonce": "fresh"})
    assert client.search("Artist - Title") == {"count": 0, "results": []}
    assert calls[1] == {"action": ["rvrs_search"], "nonce": ["fresh"], "q": ["Artist - Title"], "kind": ["audio"]}


def test_authorization_errors_are_visible_after_retry(monkeypatch):
    client = RVRemixClient()
    monkeypatch.setattr(client, "bootstrap", lambda: {"url": ENDPOINT, "nonce": "fresh"})
    def unauthorized(url, data):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
    monkeypatch.setattr(client, "_request", unauthorized)
    with pytest.raises(RuntimeError, match="401"):
        client.search("Artist")


def test_new_payload_keeps_unique_audio_with_stream_download():
    def item(name="Artist - Title.mp3", **extra):
        return {"type": "audio", "file_id": "123", "name": name, **extra}
    model = parse_search_payload({"success": True, "data": {"items": [
        item(), item(), item(type="artist"), item("Video.mp4"),
        item(file_id="../bad"), item("Tiny.mp3", size=100),
    ]}}, ENDPOINT, "token")
    assert model["count"] == 1
    row = model["results"][0]
    assert row["preview_url"] == ENDPOINT + "?action=rvrs_stream&nonce=token&file=123"
    assert row["download_url"] == row["preview_url"]
    assert row["size_bytes"] is None


@pytest.mark.parametrize("payload", [{}, {"success": True, "data": {}}, {"success": False, "data": {"message": "Expired nonce"}}])
def test_new_payload_rejects_failed_or_malformed_responses(payload):
    with pytest.raises(ValueError):
        parse_search_payload(payload, ENDPOINT, "token")


def test_quotes_artist_and_title_separately() -> None:
    assert quoted_rvremix_query("American Breed - Bend Me Shape Me") == (
        '"American Breed" "Bend Me Shape Me"'
    )


def test_quotes_single_unsplit_query() -> None:
    assert quoted_rvremix_query("American Breed") == '"American Breed"'


def test_removes_embedded_quotes() -> None:
    assert quoted_rvremix_query('"Artist" - "Title"') == '"Artist" "Title"'


def test_strips_apostrophes_from_focused_query() -> None:
    assert quoted_rvremix_query("Guns N' Roses - Sweet Child O\u2019 Mine") == (
        '"Guns N Roses" "Sweet Child O Mine"'
    )


def test_punctuation_is_unimportant_for_matching() -> None:
    assert normalized_tokens("Dr.") == ["dr"]
    assert normalized_tokens("dr") == ["dr"]
    assert normalized_tokens("AC/DC") == ["ac", "dc"]
    assert normalized_tokens("Beyoncé") == ["beyonce"]


def test_joined_and_separated_punctuation_forms_match() -> None:
    compact = {"count": 1, "results": [{"filename": "ACDC - Thunderstruck.mp3"}]}
    spaced = {"count": 1, "results": [{"filename": "AC DC - Thunderstruck.mp3"}]}
    assert filter_rvremix_model(compact, "AC/DC - Thunderstruck")["count"] == 1
    assert filter_rvremix_model(spaced, "AC/DC - Thunderstruck")["count"] == 1


def test_artist_and_title_groups_ignore_connector_words() -> None:
    assert rvremix_query_groups("Dr. Hook - Cover of the Rolling Stone") == (
        ["dr", "hook"],
        ["cover", "rolling", "stone"],
    )


def test_local_filter_requires_artist_and_title_tokens_in_visible_filename() -> None:
    model = {
        "count": 5,
        "results": [
            {"name": "025 - Dr Hook - Sexy Eyes", "filename": "025 - Dr Hook - Sexy Eyes.mp3"},
            {"name": "Dr. Hook - Sharing The Night Together", "filename": "Dr. Hook - Sharing The Night Together.mp3"},
            {
                "name": "Dr Hook and the Medicine Show - The Cover of Rolling Stone",
                "filename": "73_51 Dr Hook and the Medicine Show - The Cover of Rolling Stone.mp3",
            },
            {"name": "Rolling Stones - Cover Me", "filename": "Rolling Stones - Cover Me.mp3"},
            {"name": "DR. HOOK - COVER OF THE ROLLING STONE", "filename": "DR. HOOK - COVER OF THE ROLLING STONE.MP3"},
        ],
    }
    filtered = filter_rvremix_model(model, "dr - cover of rolling stone")
    assert filtered["count"] == 2
    assert [item["filename"] for item in filtered["results"]] == [
        "73_51 Dr Hook and the Medicine Show - The Cover of Rolling Stone.mp3",
        "DR. HOOK - COVER OF THE ROLLING STONE.MP3",
    ]


def test_dr_with_or_without_period_matches_both_forms() -> None:
    model = {
        "count": 2,
        "results": [
            {"filename": "Dr Hook - Song.mp3"},
            {"filename": "Dr. Hook - Song.mp3"},
        ],
    }
    assert filter_rvremix_model(model, "dr hook - song")["count"] == 2
    assert filter_rvremix_model(model, "dr. hook - song")["count"] == 2


def test_new_stream_url_validation():
    from djmetasearch.results import safe_preview_url
    base = ENDPOINT + "?action=rvrs_stream&nonce=token&file="
    assert safe_preview_url(base + "123")
    for url in [base + "bad", base + "123&file=456", base.replace("nonce=token", "nonce=" ) + "123",
                base.replace("rvremix.com", "evil.example") + "123",
                base.replace("rvrs_stream", "rvrs_download") + "123"]:
        assert safe_preview_url(url) == ""


def test_head_size_does_not_read_body(monkeypatch):
    from djmetasearch.rvremix import probe_stream_size
    class HeadersOnly:
        headers = {"Content-Type": "audio/mpeg", "Content-Length": "1234567"}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, *_): raise AssertionError("Must not download audio")
    requests = []
    def open_request(request, timeout):
        requests.append(request)
        return HeadersOnly()
    monkeypatch.setattr("urllib.request.urlopen", open_request)
    assert probe_stream_size(ENDPOINT + "?action=rvrs_stream&nonce=n&file=123") == 1234567
    assert [r.method for r in requests] == ["HEAD"]


def test_size_range_fallback_uses_total_not_one_byte_length(monkeypatch):
    from djmetasearch.rvremix import probe_stream_size
    class HeadersOnly:
        status = 206
        headers = {"Content-Type": "audio/mpeg", "Content-Length": "1", "Content-Range": "bytes 0-0/9876543"}
        def __enter__(self): return self
        def __exit__(self, *_): pass
    requests = []
    def open_request(request, timeout):
        requests.append(request)
        if request.method == "HEAD":
            raise urllib.error.HTTPError(request.full_url, 405, "Not allowed", {}, None)
        return HeadersOnly()
    monkeypatch.setattr("urllib.request.urlopen", open_request)
    assert probe_stream_size(ENDPOINT + "?action=rvrs_stream&nonce=n&file=123") == 9876543
    assert requests[1].get_header("Range") == "bytes=0-0"


def test_size_lookup_limits_work_discards_obsolete_queue_and_reuses_cache():
    from concurrent.futures import Future
    from djmetasearch.rvremix import RVRemixSizeLookup
    class Executor:
        def __init__(self): self.jobs = []
        def submit(self, probe, url):
            future = Future()
            self.jobs.append((url, future))
            return future
        def shutdown(self, **_): pass
    lookup = RVRemixSizeLookup()
    lookup.close()
    lookup.executor = executor = Executor()
    def row(number):
        return {"id": f"rvremix-{number}", "preview_url": ENDPOINT + f"?action=rvrs_stream&nonce=n&file={number}"}
    lookup.set_results([row(1), row(2), row(3)])
    assert lookup.poll() == {}
    assert len(executor.jobs) == 2
    lookup.set_results([row(4)])
    executor.jobs[0][1].set_result(1234567)
    assert lookup.poll() == {}
    assert len(executor.jobs) == 3
    assert "file=4" in executor.jobs[2][0]
    assert not any("file=3" in url for url, _ in executor.jobs)
    lookup.set_results([row(1)])
    assert lookup.poll() == {"rvremix-1": 1234567}
    assert lookup.poll() == {}
    assert len(executor.jobs) == 3
    lookup.set_results([row(5)])
    assert lookup.poll({row(5)["preview_url"]: 7654321}) == {"rvremix-5": 7654321}
    assert len(executor.jobs) == 3
    lookup.close()
