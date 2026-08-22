"""Convert provider response markup into small, controlled result models."""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

ALLOWED_HOST = "djpoolrecords.com"
RVREMIX_HOST = "rvremix.com"
AUDIO_EXTENSIONS = {"aac", "aif", "aiff", "flac", "m4a", "mp3", "ogg", "wav"}
VIDEO_EXTENSIONS = {"m4v", "mov", "mp4", "webm"}
RESULT_PRIORITY_TERMS = (
    "TMU",
    "CK",
    "MMP",
    "Nick Bike",
    "Johnny Flores",
    "Isaac Jordan",
    "DJ Ugeezy",
    "Tom Barker",
    "Doc Adam",
    "Deville",
    "Ivan Santana",
    "Intro",
)
INTRO_PRIORITY_RANK = RESULT_PRIORITY_TERMS.index("Intro")
PARENTHETICAL = re.compile(r"\([^)]*\)|\[[^]]*\]")
BARE_INTRO_WORDS = {"intro", "clean", "dirty"}
MATCH_STOPWORDS = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "with"}


def safe_remote_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        return ""
    if parsed.path != "/wp-admin/admin-ajax.php":
        return ""
    return value


def safe_preview_url(value: object) -> str:
    """Allow known provider preview actions and signed Dropbox media streams."""
    local_action = safe_remote_url(value)
    if local_action:
        return local_action
    if not isinstance(value, str) or not value:
        return ""
    parsed = urlparse(value)
    hostname = parsed.hostname or ""
    if parsed.scheme != "https":
        return ""
    if hostname == "dl.dropboxusercontent.com" or hostname.endswith(".dl.dropboxusercontent.com"):
        return value
    if (
        hostname == RVREMIX_HOST
        and parsed.path == "/wp-admin/admin-ajax.php"
        and parse_qs(parsed.query).get("action") == ["letsbox-stream"]
    ):
        return value
    return ""


def media_kind(filename: str, *, default: str = "other") -> str:
    suffix = PurePosixPath(filename).suffix.lower().lstrip(".")
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return default


def normalized_match_tokens(value: str, *, ignore_stopwords: bool = True) -> list[str]:
    """Create case, diacritic, and punctuation-insensitive relevance tokens."""
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    without_marks = "".join(character for character in decomposed if not unicodedata.combining(character))
    tokens = re.findall(r"[a-z0-9]+", without_marks)
    if ignore_stopwords:
        meaningful = [token for token in tokens if token not in MATCH_STOPWORDS]
        return meaningful or tokens
    return tokens


def filter_djpool_model(model: dict[str, object], query: str) -> dict[str, object]:
    """Keep only DJPool rows containing every meaningful visible query token."""
    raw_results = model.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("DJPoolRecords result model was invalid.")
    required = normalized_match_tokens(query)
    if not required:
        return {"count": 0, "results": []}
    filtered: list[dict[str, object]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        visible_list = normalized_match_tokens(
            str(item.get("name") or item.get("filename") or ""),
            ignore_stopwords=False,
        )
        visible = set(visible_list)
        if all(token in visible for token in required) or "".join(required) in "".join(visible_list):
            filtered.append(item)
    return {"count": len(filtered), "results": filtered}


def artist_and_title(name: str) -> tuple[str, str]:
    artist, separator, title = name.partition("-")
    if not separator:
        return "", name.strip()
    return artist.strip(), title.strip()


def priority_rank(name: str) -> int:
    normalized_name = name.casefold()
    for rank, term in enumerate(RESULT_PRIORITY_TERMS[:-1]):
        if term == "CK":
            matches = re.search(r"(?<!\w)ck(?!\w)", normalized_name) is not None
        else:
            matches = term.casefold() in normalized_name
        if matches:
            return rank
    _artist, title = artist_and_title(name)
    if "intro" in title.casefold():
        return INTRO_PRIORITY_RANK
    return len(RESULT_PRIORITY_TERMS)


def bare_intro_rank(title: str) -> int:
    """Put plain Intro/Clean/Dirty labels before remixes, edits, and other variants."""
    intro_position = title.casefold().find("intro")
    if intro_position < 0:
        return 1
    for match in PARENTHETICAL.finditer(title):
        words = set(re.findall(r"[a-z]+", match.group(0).casefold()))
        if match.end() <= intro_position:
            if not words.issubset({"clean", "dirty"}):
                return 1
        elif match.start() <= intro_position < match.end():
            if not words.issubset(BARE_INTRO_WORDS):
                return 1
            break
    return 0


def sort_results(results: list[dict[str, object]]) -> None:
    artist_groups: dict[str, int] = {}
    for item in results:
        name = str(item["name"])
        if priority_rank(name) != INTRO_PRIORITY_RANK:
            continue
        artist, _title = artist_and_title(name)
        artist_key = " ".join(artist.casefold().split())
        if artist_key not in artist_groups:
            artist_groups[artist_key] = len(artist_groups)

    def sort_key(item: dict[str, object]) -> tuple[int, int, int]:
        name = str(item["name"])
        rank = priority_rank(name)
        if rank != INTRO_PRIORITY_RANK:
            return rank, 0, 0
        artist, title = artist_and_title(name)
        artist_key = " ".join(artist.casefold().split())
        return rank, bare_intro_rank(title), artist_groups[artist_key]

    results.sort(key=sort_key)


def merge_result_models(models: object, *, later_title_term: str = "") -> dict[str, object]:
    """Merge parsed result models without duplicating the same remote media item."""
    if not isinstance(models, list):
        raise ValueError("Result models were not provided as a list.")
    merged: list[dict[str, object]] = []
    seen: set[tuple[str, ...]] = set()
    normalized_later_term = later_title_term.casefold().strip()
    later_title_pattern = (
        re.compile(rf"(?<!\w){re.escape(normalized_later_term)}(?!\w)", flags=re.IGNORECASE)
        if normalized_later_term
        else None
    )
    for model_index, model in enumerate(models):
        if not isinstance(model, dict) or not isinstance(model.get("results"), list):
            raise ValueError("A merged search result model was invalid.")
        for item in model["results"]:
            if not isinstance(item, dict):
                raise ValueError("A merged search result item was invalid.")
            if model_index and later_title_pattern:
                _artist, title = artist_and_title(str(item.get("name") or ""))
                if not later_title_pattern.search(title):
                    continue
            provider = str(item.get("provider") or "")
            download_url = str(item.get("download_url") or "")
            preview_url = str(item.get("preview_url") or "")
            if provider == "DJPoolRecords":
                identity = (
                    "djpool-track",
                    " ".join(str(item.get("name") or "").casefold().split()),
                    " ".join(str(item.get("size") or "").casefold().split()),
                )
            elif download_url:
                identity = ("download", download_url)
            elif preview_url:
                identity = ("preview", preview_url)
            else:
                identity = (
                    "display",
                    str(item.get("name") or "").casefold(),
                    str(item.get("filename") or "").casefold(),
                    str(item.get("size") or "").casefold(),
                )
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(dict(item))
    sort_results(merged)
    return {"count": len(merged), "results": merged}


def parse_search_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("Search response was not a JSON object.")
    if "hits" in payload:
        return parse_rest_search_payload(payload)
    html = payload.get("html")
    if not isinstance(html, str):
        raise ValueError("Search response did not contain result markup.")

    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, object]] = []
    for index, entry in enumerate(soup.select(".entry.file")):
        name_node = entry.select_one(".entry-info-name")
        size_node = entry.select_one(".entry-info-size")
        download_node = entry.select_one("a.entry_action_download[href]")
        inline_player = entry.select_one(".entry-inline-player[data-src]")
        preview_node = entry.select_one("a.entry_action_external_view[href]")
        if preview_node is None:
            preview_node = entry.select_one("a.entry_action_view[href]")
        if preview_node is None:
            # OutoftheBox commonly puts the usable media URL on the row's
            # primary link while its visible Preview action has no href.
            preview_node = entry.select_one("a.entry_link[href]")

        name = name_node.get_text(" ", strip=True) if name_node else str(entry.get("data-name") or "")
        download_name = ""
        if download_node is not None:
            download_name = str(download_node.get("download") or download_node.get("data-name") or "")
        display_name = download_name or name
        preview_value = inline_player.get("data-src") if inline_player else None
        if not preview_value and preview_node is not None:
            preview_value = preview_node.get("href")
        preview_url = safe_preview_url(preview_value)
        download_url = safe_remote_url(download_node.get("href")) if download_node else ""
        declared_type = str(inline_player.get("type") or "").lower() if inline_player else ""
        declared_kind = "audio" if declared_type.startswith("audio/") else "video" if declared_type.startswith("video/") else ""
        results.append(
            {
                "id": str(entry.get("data-id") or f"result-{index}"),
                "name": name or display_name or "Unnamed result",
                "filename": display_name or name or "download.bin",
                "size": size_node.get_text(" ", strip=True) if size_node else "",
                # The captured module is audio-only, but its returned download
                # names frequently omit .mp3/.m4a. Default those actionable
                # extensionless rows to audio instead of suppressing Preview.
                "kind": media_kind(
                    display_name or name,
                    default=declared_kind or ("audio" if preview_url or download_url else "other"),
                ),
                "mime_type": declared_type,
                "preview_url": preview_url,
                "download_url": download_url,
                "provider": "DJPoolRecords",
            }
        )

    sort_results(results)
    filescount = payload.get("filescount")
    count = filescount if isinstance(filescount, int) and filescount >= 0 else len(results)
    return {"count": count, "results": results}


def parse_rest_search_payload(payload: dict[str, object]) -> dict[str, object]:
    """Convert the authenticated DPR REST audio-search response into UI rows."""
    if payload.get("error"):
        raise ValueError("DJPoolRecords rejected the authenticated audio search.")
    hits = payload.get("hits")
    if not isinstance(hits, list):
        raise ValueError("Search response did not contain an audio hit list.")
    results: list[dict[str, object]] = []
    seen_tracks: set[tuple[str, str]] = set()
    for index, hit in enumerate(hits):
        if not isinstance(hit, dict):
            continue
        name = str(hit.get("name") or "").strip()
        extension = str(hit.get("ext") or "").strip().lower().lstrip(".")
        mime_type = str(hit.get("mime") or "").strip().lower()
        filename = name
        if extension and PurePosixPath(filename).suffix.lower() != f".{extension}":
            filename = f"{filename}.{extension}"
        kind = media_kind(filename, default="audio" if mime_type.startswith("audio/") else "other")
        if not name or kind != "audio":
            continue
        size = str(hit.get("size") or "").strip()
        track_key = (
            " ".join(name.casefold().split()),
            " ".join(size.casefold().split()),
        )
        if track_key in seen_tracks:
            continue
        seen_tracks.add(track_key)
        preview_url = safe_preview_url(hit.get("stream"))
        download_url = safe_remote_url(hit.get("download"))
        results.append(
            {
                "id": f"djpool-rest-{index}",
                "name": name,
                "filename": filename,
                "size": size,
                "kind": kind,
                "mime_type": mime_type,
                "preview_url": preview_url,
                "download_url": download_url,
                "provider": "DJPoolRecords",
            }
        )
    sort_results(results)
    return {"count": len(results), "results": results}


def parse_rvremix_payload(payload: object) -> dict[str, object]:
    """Parse a LetsBox search response, retaining only positively identified audio."""
    if not isinstance(payload, dict):
        raise ValueError("RVRemix search response was not a JSON object.")
    markup = payload.get("html")
    if not isinstance(markup, str):
        raise ValueError("RVRemix search response did not contain result markup.")

    soup = BeautifulSoup(markup, "html.parser")
    results: list[dict[str, object]] = []
    seen_filenames: set[str] = set()
    for index, entry in enumerate(soup.select(".entry.file")):
        link = entry.select_one("a.entry_link[data-name]")
        player = entry.select_one(".entry-inline-player[data-src]")
        filename = str(link.get("data-name") or "") if link else ""
        name_node = entry.select_one(".entry-info-name")
        name = name_node.get_text(" ", strip=True) if name_node else str(entry.get("data-name") or "")
        declared_type = str(player.get("type") or "").lower() if player else ""
        kind = media_kind(filename or name, default="audio" if declared_type.startswith("audio/") else "other")
        if kind != "audio" or not player:
            continue
        filename_key = " ".join((filename or name).casefold().split())
        if filename_key in seen_filenames:
            continue
        stream_url = safe_preview_url(player.get("data-src"))
        if not stream_url:
            continue
        seen_filenames.add(filename_key)
        size_node = entry.select_one(".entry-info-size")
        results.append(
            {
                "id": f"rvremix-{entry.get('data-id') or index}",
                "name": name or filename or "Unnamed result",
                "filename": filename or name or "download.mp3",
                "size": size_node.get_text(" ", strip=True) if size_node else "",
                "kind": "audio",
                "mime_type": declared_type or "audio/mpeg",
                "preview_url": stream_url,
                "download_url": stream_url,
                "provider": "RVRemix",
            }
        )

    sort_results(results)
    return {"count": len(results), "results": results}
