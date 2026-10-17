import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from djmetasearch.jiosaavn import JioSaavnClient, parse_payload, safe_song_url

URL = "https://www.jiosaavn.com/song/example/abc"


def test_parses_song_links_and_metadata_without_exposing_encrypted_media():
    song = {"id": "abc", "type": "song", "title": "Song &amp; Dance", "perma_url": URL,
            "more_info": {"album": "Album", "encrypted_media_url": "secret",
                          "artistMap": {"primary_artists": [{"name": "Artist"}]}}}
    model = parse_payload({"results": [song, song, {**song, "type": "album"}, {**song, "perma_url": "https://evil.example/song/x"}]})
    assert model["count"] == 1
    row = model["results"][0]
    assert row["name"] == "Artist - Song & Dance"
    assert row["external_url"] == URL
    assert row["provider"] == "JioSaavn"
    assert row["preview_url"] == row["download_url"] == ""
    assert "secret" not in json.dumps(model)


@pytest.mark.parametrize("url", ["http://www.jiosaavn.com/song/x", "https://www.jiosaavn.com.evil.example/song/x", "https://user@www.jiosaavn.com/song/x", "https://www.jiosaavn.com/album/x", "javascript:alert(1)"])
def test_rejects_noncanonical_links(url):
    assert safe_song_url(url) == ""


def test_search_uses_direct_endpoint_and_normalizes_artist_title(monkeypatch):
    requests = []
    def open_request(request, timeout):
        requests.append(request)
        return io.BytesIO(b'{"results": []}')
    monkeypatch.setattr("urllib.request.urlopen", open_request)
    assert JioSaavnClient().search("Artist - Title") == {"count": 0, "results": []}
    parameters = parse_qs(urlsplit(requests[0].full_url).query)
    assert parameters["q"] == ["Artist Title"]
    assert parameters["__call"] == ["search.getResults"]
    assert parameters["n"] == ["50"]


def test_provider_failure_is_visible():
    with pytest.raises(ValueError):
        parse_payload({"error": "Unavailable"})


def test_orpheus_handoff_uses_existing_app_and_encoded_song_url(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from pathlib import Path
    from djmetasearch.jiosaavn import handoff_to_orpheus
    app = tmp_path / "Applications/JioLite.app"
    app.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("subprocess.run", run)
    handoff_to_orpheus(URL)
    assert calls[0][:3] == ["/usr/bin/open", "-a", str(app)]
    assert parse_qs(urlsplit(calls[0][3]).query)["url"] == [URL]
    assert urlsplit(calls[0][3]).scheme == "jiolite"
    with pytest.raises(ValueError):
        handoff_to_orpheus("https://evil.example/song/test")
    assert len(calls) == 1
