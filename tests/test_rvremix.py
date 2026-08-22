from djmetasearch.rvremix import (
    filter_rvremix_model,
    normalized_tokens,
    quoted_rvremix_query,
    rvremix_query_groups,
)


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
