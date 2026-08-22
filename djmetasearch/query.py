"""Query construction shared by the CLI and UI launcher."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

CONTRACTION_STOPWORDS = (
    "i(?:['\u2019]?d|['\u2019]?ll|['\u2019]?m|['\u2019]?ve)",
    "you(?:['\u2019]?d|['\u2019]?ll|['\u2019]?re|['\u2019]?ve)",
    "we(?:['\u2019]?d|['\u2019]?ll|['\u2019]?re|['\u2019]?ve)",
    "they(?:['\u2019]?d|['\u2019]?ll|['\u2019]?re|['\u2019]?ve)",
    "he(?:['\u2019]?d|['\u2019]?ll|['\u2019]?s)",
    "she(?:['\u2019]?d|['\u2019]?ll|['\u2019]?s)",
    "it(?:['\u2019]?d|['\u2019]?ll|['\u2019]?s)",
    "that(?:['\u2019]?d|['\u2019]?ll|['\u2019]?s)",
    "there(?:['\u2019]?d|['\u2019]?ll|['\u2019]?s)",
    "what(?:['\u2019]?d|['\u2019]?ll|['\u2019]?s)",
    "who(?:['\u2019]?d|['\u2019]?ll|['\u2019]?s)",
    "ain['\u2019]?t",
    "aren['\u2019]?t",
    "can['\u2019]?t",
    "couldn['\u2019]?t",
    "didn['\u2019]?t",
    "doesn['\u2019]?t",
    "don['\u2019]?t",
    "hadn['\u2019]?t",
    "hasn['\u2019]?t",
    "haven['\u2019]?t",
    "isn['\u2019]?t",
    "mustn['\u2019]?t",
    "needn['\u2019]?t",
    "shouldn['\u2019]?t",
    "wasn['\u2019]?t",
    "weren['\u2019]?t",
    "won['\u2019]?t",
    "wouldn['\u2019]?t",
)
QUERY_MODIFIERS = {"intro", "clean", "dirty", "trans", "qh", "se", "segue", "short"}
APOSTROPHES = re.compile(r"['\u2018\u2019\u02bc\uff07]")


def strip_apostrophes(text: str) -> str:
    """Remove common straight and typographic apostrophes from a query."""
    return APOSTROPHES.sub("", text)


def remove_explicit(name: str) -> str:
    match = re.search(r" \(explicit\)", name, flags=re.IGNORECASE)
    return name[: match.start()] if match else name


def remove_diacritics(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def truncate_after_keywords(text: str) -> str:
    if not text:
        return text
    truncate_pos = len(text)
    for char in [",", "(", "[", "&"]:
        position = text.find(char)
        if position != -1:
            truncate_pos = min(truncate_pos, position)
    featured = re.search(r"\b(?:ft|feat)\b\.?", text, flags=re.IGNORECASE)
    if featured:
        truncate_pos = min(truncate_pos, featured.start())
    return text[:truncate_pos].rstrip()


def remove_query_stopwords(text: str) -> str:
    text = strip_apostrophes(text)
    text = re.sub(r"\b(?:dj|grupo)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(
        rf"\b(?:{'|'.join(CONTRACTION_STOPWORDS)})\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", text)


def clean_query_part(text: str) -> str:
    text = remove_explicit(text)
    text = remove_diacritics(text).replace("_", " ")
    return truncate_after_keywords(text).strip()


def filename_to_query(input_path: Path) -> str:
    name = input_path.name.rsplit(".", 1)[0] if "." in input_path.name else input_path.name
    name = remove_explicit(remove_diacritics(name)).replace("_", " ")
    artist, title = name.split(" - ", 1) if " - " in name else (name, "")
    search = f"{clean_query_part(artist)} - {clean_query_part(title)}"
    return remove_query_stopwords(search).strip()


def first_tag_value(tags: dict[str, object], key: str) -> str:
    for tag_key, value in tags.items():
        if tag_key.lower() != key:
            continue
        if isinstance(value, list):
            value = value[0] if value else ""
        return str(value).strip()
    return ""


def find_ffprobe() -> str:
    detected = shutil.which("ffprobe")
    if detected:
        return detected
    for candidate in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe"):
        if Path(candidate).exists():
            return candidate
    return ""


def audio_tags_to_query(input_path: Path) -> str:
    ffprobe = find_ffprobe()
    if not ffprobe:
        return ""
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format_tags=artist,title",
            "-of",
            "json",
            str(input_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    try:
        tags = json.loads(result.stdout).get("format", {}).get("tags", {})
    except (json.JSONDecodeError, AttributeError):
        return ""
    if not isinstance(tags, dict):
        return ""
    artist = clean_query_part(first_tag_value(tags, "artist"))
    title = clean_query_part(first_tag_value(tags, "title"))
    if not artist and not title:
        return ""
    search = f"{artist} - {title}" if artist and title else artist or title
    return remove_query_stopwords(search).strip()


def resolve_query(input_value: str | None, *, literal: bool = False, from_tags: bool = False) -> str:
    if not input_value:
        return ""
    raw = input_value.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        raw = raw[1:-1].strip()
    if literal:
        return remove_query_stopwords(raw).strip()
    possible_path = Path(raw).expanduser()
    if possible_path.exists():
        if from_tags:
            tag_query = audio_tags_to_query(possible_path)
            if tag_query:
                return tag_query
        return filename_to_query(possible_path)
    return remove_query_stopwords(raw).strip()


def variant_query(query: str, suffix: str) -> str:
    query = strip_apostrophes(query).strip()
    suffix = suffix.strip()
    if not query:
        return suffix
    words = query.split()
    last_word = words[-1].lower()
    if last_word == suffix.lower():
        return query
    if last_word in QUERY_MODIFIERS:
        words.pop()
    return " ".join([*words, suffix])


def default_query(query: str) -> str:
    """Normalize the launch query without adding a variant modifier."""
    return strip_apostrophes(query).strip()


def clear_query_modifier(query: str) -> str:
    words = strip_apostrophes(query).strip().split()
    if words and words[-1].lower() in QUERY_MODIFIERS:
        words.pop()
    return " ".join(words)
