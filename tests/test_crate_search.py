from __future__ import annotations

from djmetasearch.crate_search import CrateSearchClient, formatted_size, safe_media_reference


def test_search_maps_audio_pages_filters_filename_matches_and_small_files(monkeypatch) -> None:
    client = CrateSearchClient("secret")
    requests: list[dict[str, object]] = []
    pages = [
        {
            "total": 3,
            "count": 2,
            "results": [
                {
                    "id": "6080796180102",
                    "name": "De La Soul - Saturday 108.mp3",
                    "type": "audio",
                    "folder": "Hip Hop",
                    "path": "Hip Hop/De La Soul - Saturday 108.mp3",
                    "size": 5_815_654,
                    "modified": 1_508_286_122_000,
                },
                {
                    "id": "tiny",
                    "name": "De La Soul - Saturday clip.mp3",
                    "type": "audio",
                    "size": 900_000,
                },
            ],
        },
        {
            "total": 3,
            "count": 1,
            "results": [{
                "id": "folder-only",
                "name": "Different Artist.mp3",
                "type": "audio",
                "folder": "De La Soul Saturday",
                "size": 4_000_000,
            }],
        },
    ]

    def request(parameters: dict[str, object]) -> dict[str, object]:
        requests.append(parameters)
        return pages[len(requests) - 1]

    monkeypatch.setattr(client, "_request_json", request)
    model = client.search("De La Soul - Saturday")

    assert [request["offset"] for request in requests] == [0, 2]
    assert model["count"] == 1
    result = model["results"][0]
    assert result["provider_item_id"] == "6080796180102"
    assert result["display_name"] == "De La Soul - Saturday"
    assert result["bpm"] == 108
    assert result["size_bytes"] == 5_815_654
    assert result["provider"] == "DJFolders"
    assert result["resolvable"] is True
    assert result["preview_url"] == ""


def test_resolve_returns_constrained_direct_media_urls(monkeypatch) -> None:
    client = CrateSearchClient("secret key")
    monkeypatch.setattr(client, "_request_json", lambda _parameters: {
        "id": "123",
        "mimeType": "audio/mpeg",
        "stream": "?action=stream&id=123&key=server-value",
        "download": "?action=download&id=123&key=server-value",
    })

    resolved = client.resolve("123")

    assert resolved["direct_stream"] is True
    assert resolved["preview_url"].startswith("https://pod.djpanaflex.com/crate-search/?action=stream&id=123")
    assert "key=secret+key" in resolved["preview_url"]
    assert "action=download" in resolved["download_url"]


def test_rejects_cross_origin_or_wrong_track_media_reference() -> None:
    assert safe_media_reference(
        "https://evil.example/?action=stream&id=123", action="stream", track_id="123", api_key="key"
    ) == ""
    assert safe_media_reference(
        "?action=stream&id=other", action="stream", track_id="123", api_key="key"
    ) == ""


def test_formats_exact_byte_sizes_for_display() -> None:
    assert formatted_size(512) == "512 B"
    assert formatted_size(1536) == "1.5 KB"
    assert formatted_size(5 * 1024 * 1024) == "5 MB"
