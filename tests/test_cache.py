import json
from pathlib import Path

from djmetasearch.cache import load_cache, make_cache, save_cache, validate_cache


SEARCH_URL = "https://djpoolrecords.com/wp-json/dpr-search/v1/files"
NONCE = "safe123nonce"


def test_cache_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "bootstrap.json"
    value = make_cache(SEARCH_URL, NONCE)
    save_cache(path, value)
    assert load_cache(path) == value


def test_wrong_schema_is_stale() -> None:
    value = make_cache(SEARCH_URL, NONCE)
    value["schema"] = 999
    assert validate_cache(value) is None


def test_external_endpoint_is_rejected() -> None:
    value = make_cache(SEARCH_URL, NONCE)
    value["url"] = "https://example.com/wp-json/dpr-search/v1/files"
    assert validate_cache(value) is None


def test_corrupt_cache_loads_as_missing(tmp_path: Path) -> None:
    path = tmp_path / "bootstrap.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_cache(path) is None
