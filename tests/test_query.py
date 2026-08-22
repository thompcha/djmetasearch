from pathlib import Path

from djmetasearch import query


def test_filename_query_removes_diacritics_feature_and_explicit(tmp_path: Path) -> None:
    audio = tmp_path / "DJ Ártist feat. Guest - Tïtle (Remix) (Explicit).mp3"
    audio.touch()
    assert query.filename_to_query(audio) == "Artist - Title"


def test_filename_query_removes_group_prefix_and_extra_artist(tmp_path: Path) -> None:
    audio = tmp_path / "Grupo Example & Friend - Song.mp3"
    audio.touch()
    assert query.filename_to_query(audio) == "Example - Song"


def test_literal_query_removes_stopwords() -> None:
    assert query.resolve_query("DJ Example - Don't Stop", literal=True) == "Example - Stop"


def test_query_strips_straight_and_typographic_apostrophes() -> None:
    assert query.resolve_query("Lil' Wayne - A Milli", literal=True) == "Lil Wayne - A Milli"
    assert query.resolve_query("Guns N\u2019 Roses - Sweet Child O\u2018 Mine", literal=True) == (
        "Guns N Roses - Sweet Child O Mine"
    )
    assert query.resolve_query("Artist\u02bcs - Title\uff07s", literal=True) == "Artists - Titles"


def test_query_helpers_strip_apostrophes() -> None:
    assert query.variant_query("Lil' Wayne - A Milli", "intro") == "Lil Wayne - A Milli intro"
    assert query.default_query("Lil\u2019 Wayne - A Milli") == "Lil Wayne - A Milli"
    assert query.clear_query_modifier("Lil\u2018 Wayne - A Milli intro") == "Lil Wayne - A Milli"


def test_tag_query_is_preferred(monkeypatch, tmp_path: Path) -> None:
    audio = tmp_path / "fallback filename.mp3"
    audio.touch()
    monkeypatch.setattr(query, "audio_tags_to_query", lambda _path: "Tagged Artist - Tagged Title")
    assert query.resolve_query(str(audio), from_tags=True) == "Tagged Artist - Tagged Title"


def test_tag_query_falls_back_to_filename(monkeypatch, tmp_path: Path) -> None:
    audio = tmp_path / "Artist - Title.mp3"
    audio.touch()
    monkeypatch.setattr(query, "audio_tags_to_query", lambda _path: "")
    assert query.resolve_query(str(audio), from_tags=True) == "Artist - Title"


def test_variant_query_does_not_duplicate_suffix() -> None:
    assert query.variant_query("Artist - Title", "intro") == "Artist - Title intro"
    assert query.variant_query("Artist - Title INTRO", "intro") == "Artist - Title INTRO"


def test_variant_query_replaces_existing_modifier() -> None:
    assert query.variant_query("Artist - Title intro", "clean") == "Artist - Title clean"
    assert query.variant_query("Artist - Title Dirty", "trans") == "Artist - Title trans"
    assert query.variant_query("intro", "clean") == "clean"
    assert query.variant_query("Artist - Title intro", "QH") == "Artist - Title QH"
    assert query.variant_query("Artist - Title SE", "segue") == "Artist - Title segue"
    assert query.variant_query("Artist - Title SE", "short") == "Artist - Title short"


def test_default_query_does_not_add_a_modifier() -> None:
    assert query.default_query("Artist - Title") == "Artist - Title"
    assert query.default_query("Artist - Title intro") == "Artist - Title intro"
    assert query.default_query("Artist - Title clean") == "Artist - Title clean"
    assert query.default_query("Artist - Title QH") == "Artist - Title QH"
    assert query.default_query("Artist - Title segue") == "Artist - Title segue"
    assert query.default_query("Artist - Title Short") == "Artist - Title Short"
    assert query.default_query("") == ""


def test_clear_query_modifier_removes_only_a_trailing_modifier() -> None:
    assert query.clear_query_modifier("Artist - Title intro") == "Artist - Title"
    assert query.clear_query_modifier("Artist - Title Clean") == "Artist - Title"
    assert query.clear_query_modifier("Artist - Title QH") == "Artist - Title"
    assert query.clear_query_modifier("Artist - Title segue") == "Artist - Title"
    assert query.clear_query_modifier("Artist - Title short") == "Artist - Title"
    assert query.clear_query_modifier("Intro Artist - Title") == "Intro Artist - Title"
