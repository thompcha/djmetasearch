import json
from pathlib import Path

import pytest

from djmetasearch.results import (
    filter_djpool_model,
    is_labeled_vanilla_version,
    is_vanilla_version,
    merge_result_models,
    parse_rvremix_payload,
    parse_search_payload,
    parsed_size_bytes,
    result_meets_minimum_size,
    safe_preview_url,
    safe_remote_url,
    version_family_key,
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


@pytest.mark.parametrize("size", ["999 KB", "0.99 MB", "1,048,575 bytes", 1_048_575])
def test_known_results_smaller_than_one_megabyte_are_omitted(size: object) -> None:
    assert not result_meets_minimum_size({"size": size})


@pytest.mark.parametrize("size", ["1 MB", "1024 KB", "1 MiB", "", None, "unknown"])
def test_one_megabyte_and_unknown_sizes_are_retained(size: object) -> None:
    assert result_meets_minimum_size({"size": size})


def test_size_parser_handles_provider_units() -> None:
    assert parsed_size_bytes("512 KB") == 512 * 1024
    assert parsed_size_bytes("1.5 MB") == 1.5 * 1024 * 1024
    assert parsed_size_bytes("2 GB") == 2 * 1024**3


def test_rest_audio_search_omits_sub_megabyte_hits() -> None:
    model = parse_search_payload({
        "hits": [
            {"name": "Tiny", "ext": "mp3", "size": "842 KB", "mime": "audio/mpeg"},
            {"name": "Keep", "ext": "mp3", "size": "1 MB", "mime": "audio/mpeg"},
            {"name": "Unknown", "ext": "mp3", "size": "", "mime": "audio/mpeg"},
        ]
    })
    assert [item["name"] for item in model["results"]] == ["Keep", "Unknown"]


def test_merge_drops_sub_megabyte_rows_from_async_models() -> None:
    tiny = {"name": "Tiny", "filename": "tiny.mp3", "size": "900 KB"}
    normal = {"name": "Normal", "filename": "normal.mp3", "size": "4 MB"}

    assert merge_result_models([{"count": 2, "results": [tiny, normal]}])["results"] == [normal]


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


def test_rvremix_parser_omits_sub_megabyte_audio() -> None:
    payload = {
        "filescount": 2,
        "html": (
            "<div class='entry file' data-id='tiny'><span class='entry-info-name'>Tiny</span>"
            "<span class='entry-info-size'>999 KB</span>"
            "<a class='entry_link' data-name='Tiny.mp3'></a>"
            "<div class='entry-inline-player' type='audio/mpeg' "
            "data-src='https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-stream&amp;id=tiny'></div></div>"
            "<div class='entry file' data-id='normal'><span class='entry-info-name'>Normal</span>"
            "<span class='entry-info-size'>1 MB</span>"
            "<a class='entry_link' data-name='Normal.mp3'></a>"
            "<div class='entry-inline-player' type='audio/mpeg' "
            "data-src='https://rvremix.com/wp-admin/admin-ajax.php?action=letsbox-stream&amp;id=normal'></div></div>"
        ),
    }

    model = parse_rvremix_payload(payload)

    assert [item["filename"] for item in model["results"]] == ["Normal.mp3"]


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
        "Song - CK Intro",
        "Ordinary Result",
        "Another Ordinary Result",
    ]


def test_ck_is_a_contextual_label_not_a_global_priority() -> None:
    names = [
        "Artist - Song (Sickmix Intro)",
        "Artist - Song (CKOne Edit)",
        "Artist - Song (ck Edit)",
        "Artist - Song (Ck-Cut)",
        "Artist - Song",
        "Artist - Song (Smassh Edit) Clean CK Cut",
        "Artist - Song (Smassh Edit) Clean",
        "Artist - Song (Intro Clean)",
    ]
    html = "".join(
        f"<div class='entry file'><span class='entry-info-name'>{name}</span></div>"
        for name in names
    )
    model = parse_search_payload({"filescount": len(names), "html": html})
    ordered = [item["name"] for item in model["results"]]
    assert ordered == [names[7], names[3], names[4], names[0], names[1], names[2], names[5], names[6]]
    assert ordered.index(names[3]) < ordered.index(names[5])
    assert abs(ordered.index(names[5]) - ordered.index(names[6])) == 1


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
        "Intro Band - Ordinary Song",
        "Akon, Snoop Dogg - I Wanna Love You (Sickmix Intro) (Clean) 100",
        "Beta & Guest - Track (Remix) (Intro)",
        "Alpha - Track (Edit) (Clean Intro)",
        "Alpha - Track (Club Mix) (Intro)",
        "Ordinary Result",
    ]


def test_named_edit_copies_are_grouped_across_delivery_variants() -> None:
    names = [
        "Walk The Moon - Shut Up And Dance (Ordinary Mix) 128",
        "Walk The Moon Vs Codeko - Shut Up And Dance (Smassh Edit) Clean Ck Cut 128",
        "Walk The Moon - Shut Up And Dance (Other Edit) 128",
        "Walk The Moon Vs Codeko - Shut Up And Dance (Smassh Edit) (Break Fill) Clean 124",
        "76_35 - Walk The Moon Vs Codeko - Shut Up And Dance (Smassh Edit) Clean 124",
        "Walk The Moon - Shut Up And Dance (Another Remix) 128",
    ]
    html = "".join(
        f"<div class='entry file'><span class='entry-info-name'>{name}</span></div>"
        for name in names
    )

    model = parse_search_payload({"filescount": len(names), "html": html})

    assert [item["name"] for item in model["results"]] == [
        names[0],
        names[1],  # CK stays with Smassh but no longer promotes the family.
        names[3],
        names[4],
        names[2],
        names[5],
    ]


def test_version_family_retains_named_edit_but_ignores_punctuation_and_copy_labels() -> None:
    plain = "Walk The Moon Vs Codeko - Shut Up And Dance (Smassh Edit) Clean 124"
    break_fill = "76_35 Walk The Moon Vs. Codeko - Shut Up & Dance (Smassh Edit)(Break Fill) [Clean] 124.mp3"
    punctuation = "Walk The Moon Vs. Codeko - Shut Up & Dance (Smassh Edit) Ck Cut Clean 124"
    different_edit = "Walk The Moon Vs Codeko - Shut Up And Dance (Other Edit) Clean 124"

    assert version_family_key(plain) == version_family_key(break_fill)
    assert version_family_key(plain) == version_family_key(punctuation)
    assert version_family_key(plain) != version_family_key(different_edit)


def test_named_remix_family_ignores_provider_title_and_transition_wording() -> None:
    white_panda = [
        "Walk The Moon - Shut Up & Dance (Og To White Panda Remix) (Intro Clean) 128",
        "Walk The Moon - Shut Up And Dance (White Panda Remix) (Clean) 128",
        "Walk The Moon - Shut Up And Dance With Me (The White Panda Remix) 128",
    ]
    unrelated = "Walk The Moon - Shut Up And Dance (White Panda Intro) 128"

    assert len({version_family_key(name) for name in white_panda}) == 1
    assert version_family_key(white_panda[0]) != version_family_key(unrelated)


def test_named_remix_versions_become_adjacent_without_alphabetizing_other_families() -> None:
    names = [
        "Walk The Moon - Shut Up & Dance (Og To White Panda Remix) (Intro Clean) 128",
        "Walk The Moon - Shut Up And Dance (Other Edit) 128",
        "Walk The Moon - Shut Up And Dance With Me (The White Panda Remix) 128",
        "Walk The Moon - Shut Up And Dance (Another Remix) 128",
        "Walk The Moon - Shut Up And Dance (White Panda Remix) (Clean) 128",
    ]
    html = "".join(
        f"<div class='entry file'><span class='entry-info-name'>{name}</span></div>"
        for name in names
    )

    model = parse_search_payload({"filescount": len(names), "html": html})

    assert [item["name"] for item in model["results"]] == [
        names[0],
        names[2],
        names[4],
        names[1],
        names[3],
    ]


def test_vanilla_versions_follow_all_preferred_results_despite_list_numbers() -> None:
    names = [
        "Artist - Song (Other Remix) 128",
        "025 - Artist - Song 128",
        "Artist - Song (CK Cut) 128",
        "76_35 Artist - Song (Clean) 128",
        "Song - Artist (Original Mix) 128",
        "Artist Vs Guest - Song (Bootleg) 128",
        "Artist - Song (Intro Clean) 128",
    ]
    html = "".join(
        f"<div class='entry file'><span class='entry-info-name'>{name}</span></div>"
        for name in names
    )

    model = parse_search_payload({"filescount": len(names), "html": html})

    assert [item["name"] for item in model["results"]] == [
        names[6],
        names[2],
        names[3],
        names[1],
        names[4],
        names[0],
        names[5],
    ]


def test_vanilla_detection_ignores_catalog_prefix_but_rejects_named_versions() -> None:
    assert is_vanilla_version("025 - Artist - Song 128.mp3")
    assert is_vanilla_version("76_35 Artist - Song (Clean) 128")
    assert is_vanilla_version("44. Artist - Song 128")
    assert is_vanilla_version("Artist - Song (Original Mix) 128")
    assert not is_vanilla_version("Artist - Song (Radio Edit) 128")
    assert not is_vanilla_version("Artist Vs Guest - Song 128")
    assert not is_vanilla_version("Acca - Artist - Song 128")
    assert not is_vanilla_version("Inst - Artist - Song 128")
    assert not is_vanilla_version("Artist - Song-Se-Dollar 128")
    assert not is_vanilla_version(
        "Almost Monday Walk The Moon Wet Leg - Can't Slow Down Shut Up And Dance Catch These Fists (Clean) 127"
    )


def test_labeled_vanilla_precedes_plain_vanilla_then_intro_edits() -> None:
    names = [
        "Artist - Song (Intro Remix) 128",
        "025 - Artist - Song 128",
        "Artist - Song (Other Edit) 128",
        "Artist - Song (TMU Throwback Edit) 128",
        "76_35 Artist - Song (Intro Clean) 128",
        "44. Artist - Song (Dirty) 128",
        "Artist - Song (Clean) 128",
        "Artist - Song (Sickmix Intro) (Clean) 128",
        "Artist - Song (Original Mix) 128",
    ]
    html = "".join(
        f"<div class='entry file'><span class='entry-info-name'>{name}</span></div>"
        for name in names
    )

    model = parse_search_payload({"filescount": len(names), "html": html})

    assert [item["name"] for item in model["results"]] == [
        names[3],
        names[4],
        names[5],
        names[6],
        names[1],
        names[8],
        names[0],
        names[7],
        names[2],
    ]
    assert is_labeled_vanilla_version(names[4])
    assert is_labeled_vanilla_version(names[5])
    assert not is_labeled_vanilla_version(names[1])


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


def test_trans_modifier_also_accepts_transition_as_a_whole_word() -> None:
    trans = {"name": "Artist - Song (Trans Up Edit)", "filename": "trans.mp3", "size": "5 MB"}
    transition = {
        "name": "Artist - Song (Transition 105-128)",
        "filename": "transition.mp3",
        "size": "6 MB",
    }
    transitions = {
        "name": "Artist - Song (Transitions Edit)",
        "filename": "transitions.mp3",
        "size": "7 MB",
    }
    false_positive = {
        "name": "Artist - Song (Transport Remix)",
        "filename": "transport.mp3",
        "size": "8 MB",
    }

    merged = merge_result_models(
        [
            {"count": 1, "results": [trans]},
            {"count": 3, "results": [transition, transitions, false_positive]},
        ],
        later_title_term="trans",
    )

    assert [item["name"] for item in merged["results"]] == [
        trans["name"],
        transition["name"],
        transitions["name"],
    ]
