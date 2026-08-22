import json
from pathlib import Path

import pytest

from djmetasearch.results import (
    filter_djpool_model,
    merge_result_models,
    parse_rvremix_payload,
    parse_search_payload,
    safe_preview_url,
    safe_remote_url,
)


FIXTURE = Path(__file__).parent / "fixtures" / "search_audio_video.json"


def test_parses_audio_video_and_missing_actions() -> None:
    model = parse_search_payload(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert model["count"] == 3
    assert [item["kind"] for item in model["results"]] == ["audio", "video", "other"]
    assert model["results"][0]["filename"] == "Artist - Song"
    assert model["results"][0]["mime_type"] == "audio/mpeg"
    assert model["results"][0]["preview_url"].startswith("https://ucfixture.dl.dropboxusercontent.com/")
    assert model["results"][2]["preview_url"] == ""
    assert model["results"][2]["download_url"] == ""


def test_zero_results() -> None:
    assert parse_search_payload({"filescount": 0, "html": "<div class='files-container'></div>"}) == {
        "count": 0,
        "results": [],
    }


def test_parses_rest_audio_search_hits() -> None:
    stream = "https://djpoolrecords.com/wp-admin/admin-ajax.php?action=outofthebox-stream&id=audio"
    download = "https://djpoolrecords.com/wp-admin/admin-ajax.php?action=outofthebox-download&id=audio"
    model = parse_search_payload({
        "hits": [{
            "name": "Post Malone - Congratulations (Intro)",
            "ext": "mp3",
            "size": "10 MB",
            "mime": "audio/mpeg",
            "stream": stream,
            "download": download,
        }],
        "estimatedTotalHits": 1,
        "hasMore": False,
        "processingTimeMs": 3,
    })
    assert model == {
        "count": 1,
        "results": [{
            "id": "djpool-rest-0",
            "name": "Post Malone - Congratulations (Intro)",
            "filename": "Post Malone - Congratulations (Intro).mp3",
            "size": "10 MB",
            "kind": "audio",
            "mime_type": "audio/mpeg",
            "preview_url": stream,
            "download_url": download,
            "provider": "DJPoolRecords",
        }],
    }


def test_rest_audio_search_rejects_non_audio_and_untrusted_urls() -> None:
    model = parse_search_payload({
        "hits": [
            {"name": "Video", "ext": "mp4", "mime": "video/mp4"},
            {
                "name": "Audio",
                "ext": "mp3",
                "mime": "audio/mpeg",
                "stream": "https://evil.example/audio.mp3",
                "download": "javascript:alert(1)",
            },
        ]
    })
    assert model["count"] == 1
    assert model["results"][0]["preview_url"] == ""
    assert model["results"][0]["download_url"] == ""


def test_rest_audio_search_surfaces_membership_or_nonce_rejection() -> None:
    with pytest.raises(ValueError, match="rejected"):
        parse_search_payload({"hits": [], "estimatedTotalHits": 0, "error": "members_only"})


def test_rest_audio_search_deduplicates_identical_name_and_size() -> None:
    model = parse_search_payload({
        "hits": [
            {"name": "Artist - Song", "ext": "mp3", "size": "10 MB", "mime": "audio/mpeg"},
            {"name": " artist  -  song ", "ext": "mp3", "size": "10 mb", "mime": "audio/mpeg"},
            {"name": "Artist - Song", "ext": "mp3", "size": "11 MB", "mime": "audio/mpeg"},
        ]
    })
    assert model["count"] == 2
    assert [item["size"] for item in model["results"]] == ["10 MB", "11 MB"]


def test_merge_deduplicates_djpool_name_and_size_across_pages() -> None:
    first = {
        "name": "Artist - Song",
        "filename": "Artist - Song.mp3",
        "size": "10 MB",
        "download_url": "https://djpoolrecords.com/wp-admin/admin-ajax.php?id=one",
        "provider": "DJPoolRecords",
    }
    duplicate = {**first, "download_url": "https://djpoolrecords.com/wp-admin/admin-ajax.php?id=two"}
    different_size = {**first, "size": "11 MB", "download_url": "https://djpoolrecords.com/wp-admin/admin-ajax.php?id=three"}
    merged = merge_result_models([
        {"count": 1, "results": [first]},
        {"count": 2, "results": [duplicate, different_size]},
    ])
    assert merged["results"] == [first, different_size]


def test_djpool_relevance_requires_all_meaningful_query_tokens() -> None:
    exact = {"name": "Shania Twain - Man! I Feel Like a Woman! (Intro)", "provider": "DJPoolRecords"}
    wrong_song = {"name": "Shania Twain - Any Man of Mine", "provider": "DJPoolRecords"}
    other_artist = {"name": "Cover Band - Man I Feel Like A Woman", "provider": "DJPoolRecords"}
    filtered = filter_djpool_model(
        {"count": 3, "results": [wrong_song, exact, other_artist]},
        "Shania Twain Man I Feel Like A Woman",
    )
    assert filtered == {"count": 1, "results": [exact]}


def test_rvremix_parser_keeps_audio_and_rejects_video() -> None:
    payload = {
        "filescount": 2,
        "html": (
            "<div class='entry file' data-id='audio'><span class='entry-info-name'>Artist - Audio</span>"
            "<a class='entry_link' data-name='Artist - Audio.mp3'></a>"
            "<div class='entry-inline-player' type='audio/mpeg' "
            "data-src='https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-stream&amp;id=audio'></div></div>"
            "<div class='entry file' data-id='video'><span class='entry-info-name'>Artist - Video</span>"
            "<a class='entry_link' data-name='Artist - Video.mp4'></a></div>"
        ),
    }
    model = parse_rvremix_payload(payload)
    assert model["count"] == 1
    assert model["results"][0]["filename"] == "Artist - Audio.mp3"
    assert model["results"][0]["provider"] == "RVRemix"
    assert model["results"][0]["download_url"] == model["results"][0]["preview_url"]


@pytest.mark.parametrize(
    "url",
    [
        "http://djpoolrecords.com/wp-admin/admin-ajax.php?action=x",
        "https://evil.example/wp-admin/admin-ajax.php?action=x",
        "https://djpoolrecords.com/not-ajax?action=x",
        "javascript:alert(1)",
    ],
)
def test_rejects_untrusted_result_urls(url: str) -> None:
    assert safe_remote_url(url) == ""


def test_requires_result_markup() -> None:
    with pytest.raises(ValueError, match="result markup"):
        parse_search_payload({"filescount": 1})


def test_allows_only_trusted_dropbox_preview_hosts() -> None:
    trusted = "https://uc123.dl.dropboxusercontent.com/cd/0/get/file?token=signed"
    assert safe_preview_url(trusted) == trusted
    assert safe_preview_url("https://dl.dropboxusercontent.com/file")
    assert safe_preview_url("https://dl.dropboxusercontent.com.evil.example/file") == ""
    assert safe_remote_url(trusted) == ""


def test_allows_only_rvremix_audio_stream_actions() -> None:
    stream = "https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-stream&id=123"
    assert safe_preview_url(stream) == stream
    assert safe_preview_url("https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-preview&id=123") == ""
    assert safe_preview_url("https://evil.example/wp-admin/admin-ajax.php?action=letsbox-stream&id=123") == ""


def test_priority_terms_move_to_top_in_configured_order_stably() -> None:
    names = [
        "Ordinary Result",
        "Song - nick bike edit 1",
        "Song - MMP Remix",
        "Song - TMU Throwback",
        "Song - TMU Intro",
        "Song - Nick Bike Edit 2",
        "Another Ordinary Result",
        "Song - CK Intro",
        "Song - Johnny Flores",
        "Song - Isaac Jordan",
        "Song - DJ Ugeezy",
        "Song - Tom Barker",
        "Song - Doc Adam",
        "Song - Deville",
        "Song - Ivan Santana",
        "Song - Clean Intro 1",
        "Song - Dirty Intro 2",
    ]
    html = "".join(
        f"<div class='entry file' data-id='{index}'><span class='entry-info-name'>{name}</span></div>"
        for index, name in enumerate(names)
    )
    model = parse_search_payload({"filescount": len(names), "html": html})
    assert [item["name"] for item in model["results"]] == [
        "Song - TMU Throwback",
        "Song - TMU Intro",
        "Song - CK Intro",
        "Song - MMP Remix",
        "Song - nick bike edit 1",
        "Song - Nick Bike Edit 2",
        "Song - Johnny Flores",
        "Song - Isaac Jordan",
        "Song - DJ Ugeezy",
        "Song - Tom Barker",
        "Song - Doc Adam",
        "Song - Deville",
        "Song - Ivan Santana",
        "Song - Clean Intro 1",
        "Song - Dirty Intro 2",
        "Ordinary Result",
        "Another Ordinary Result",
    ]


def test_ck_priority_is_case_insensitive_and_requires_a_whole_word() -> None:
    names = [
        "Artist - Sickmix Intro",
        "Artist - CKOne Edit",
        "Artist - ck Edit",
        "Artist - (Ck-Cut)",
        "Artist - Ordinary",
    ]
    html = "".join(
        f"<div class='entry file'><span class='entry-info-name'>{name}</span></div>"
        for name in names
    )
    model = parse_search_payload({"filescount": len(names), "html": html})
    assert [item["name"] for item in model["results"]] == [
        "Artist - ck Edit",
        "Artist - (Ck-Cut)",
        "Artist - Sickmix Intro",
        "Artist - CKOne Edit",
        "Artist - Ordinary",
    ]


def test_intro_priority_groups_first_hyphen_artists_with_bare_versions_first() -> None:
    names = [
        "Ordinary Result",
        "Akon, Snoop Dogg - I Wanna Love You (Sickmix Intro) (Clean) 100",
        "Akon - I Wanna Love You (Intro) (Clean) 100",
        "Beta & Guest - Track (Remix) (Intro)",
        "Alpha - Track (Edit) (Clean Intro)",
        "Beta & Guest - Track (Intro)",
        "Alpha - Track (Dirty Intro)",
        "Beta & Guest - Track (Clean Intro)",
        "Alpha - Track (Club Mix) (Intro)",
        "Intro Band - Ordinary Song",
        "Beta & Guest - Track - Dirty Intro",
    ]
    html = "".join(
        f"<div class='entry file' data-id='{index}'><span class='entry-info-name'>{name}</span></div>"
        for index, name in enumerate(names)
    )

    model = parse_search_payload({"filescount": len(names), "html": html})

    assert [item["name"] for item in model["results"]] == [
        "Akon - I Wanna Love You (Intro) (Clean) 100",
        "Beta & Guest - Track (Intro)",
        "Beta & Guest - Track (Clean Intro)",
        "Beta & Guest - Track - Dirty Intro",
        "Alpha - Track (Dirty Intro)",
        "Akon, Snoop Dogg - I Wanna Love You (Sickmix Intro) (Clean) 100",
        "Beta & Guest - Track (Remix) (Intro)",
        "Alpha - Track (Edit) (Clean Intro)",
        "Alpha - Track (Club Mix) (Intro)",
        "Ordinary Result",
        "Intro Band - Ordinary Song",
    ]


def test_merge_result_models_deduplicates_and_reapplies_priority_sort() -> None:
    ordinary = {"name": "Artist - Ordinary", "filename": "ordinary.mp3", "size": "5 MB", "download_url": ""}
    intro = {"name": "Artist - Song (Intro)", "filename": "intro.mp3", "size": "6 MB", "download_url": ""}
    tmu = {"name": "Artist - Song (TMU)", "filename": "tmu.mp3", "size": "7 MB", "download_url": ""}

    merged = merge_result_models(
        [
            {"count": 2, "results": [intro, ordinary]},
            {"count": 3, "results": [ordinary, tmu, intro]},
        ]
    )

    assert merged["count"] == 3
    assert [item["name"] for item in merged["results"]] == [
        "Artist - Song (TMU)",
        "Artist - Song (Intro)",
        "Artist - Ordinary",
    ]


def test_merge_can_limit_broader_results_to_intro_titles() -> None:
    exact = {"name": "Artist - Exact Match", "filename": "exact.mp3", "size": "5 MB"}
    rescued_intro = {"name": "Artist - Rescued (Intro)", "filename": "intro.mp3", "size": "6 MB"}
    broad_ordinary = {"name": "Artist - Ordinary", "filename": "ordinary.mp3", "size": "7 MB"}

    merged = merge_result_models(
        [
            {"count": 1, "results": [exact]},
            {"count": 2, "results": [rescued_intro, broad_ordinary]},
        ],
        later_title_term="intro",
    )

    assert merged["count"] == 2
    assert [item["name"] for item in merged["results"]] == [
        "Artist - Rescued (Intro)",
        "Artist - Exact Match",
    ]


def test_broader_term_filter_matches_standalone_modifier_only() -> None:
    exact = {"name": "Artist - Exact", "filename": "exact.mp3", "size": "5 MB"}
    se = {"name": "Artist - Song (SE)", "filename": "se.mp3", "size": "6 MB"}
    false_positive = {"name": "Artist - House Mix", "filename": "house.mp3", "size": "7 MB"}

    merged = merge_result_models(
        [
            {"count": 1, "results": [exact]},
            {"count": 2, "results": [se, false_positive]},
        ],
        later_title_term="se",
    )

    assert [item["name"] for item in merged["results"]] == ["Artist - Exact", "Artist - Song (SE)"]
