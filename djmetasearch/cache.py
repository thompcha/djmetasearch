"""Validated local bootstrap metadata cache."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

CACHE_SCHEMA = 2


def validate_cache(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or value.get("schema") != CACHE_SCHEMA:
        return None
    url = value.get("url")
    nonce = value.get("nonce")
    if not isinstance(url, str) or not isinstance(nonce, str) or not nonce:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "djpoolrecords.com":
        return None
    if parsed.path != "/wp-json/dpr-search/v1/files" or parsed.query:
        return None
    if not all(character.isalnum() for character in nonce):
        return None
    return value


def load_cache(path: Path) -> dict[str, object] | None:
    try:
        return validate_cache(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def make_cache(url: str, nonce: str) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": CACHE_SCHEMA,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "nonce": nonce,
    }
    if validate_cache(value) is None:
        raise ValueError("Captured bootstrap metadata was incomplete or unsafe.")
    return value


def save_cache(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
