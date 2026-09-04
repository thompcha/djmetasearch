"""Convert provider response markup into small, controlled result models."""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

ALLOWED_HOST = "djpoolrecords.com"
RVREMIX_HOST = "rvremix.com"
CRATE_SEARCH_HOST = "pod.djpanaflex.com"
CRATE_SEARCH_PATH = "/crate-search/"
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
VANILLA_PRIORITY_RANK = INTRO_PRIORITY_RANK + 1
INTRO_VARIANT_PRIORITY_RANK = VANILLA_PRIORITY_RANK + 1
OTHER_PRIORITY_RANK = INTRO_VARIANT_PRIORITY_RANK + 1
PARENTHETICAL = re.compile(r"\([^)]*\)|\[[^]]*\]")
BARE_INTRO_WORDS = {"and", "clean", "dirty", "intro", "outro", "to"}
VANILLA_DELIVERY_WORDS = {
    "and",
    "bpm",
    "ck",
    "clean",
    "cut",
    "dirty",
    "explicit",
    "intro",
    "outro",
    "to",
}
MATCH_STOPWORDS = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "with"}
VERSION_COPY_QUALIFIERS = {
    "break fill",
    "clean",
    "clean ck cut",
    "ck cut",
    "crate cuts",
    "dirty",
    "explicit",
    "extended",
    "hook first",
    "instrumental",
    "main",
    "no outro",
    "quickhitter",
    "qh",
    "se",
}
TRAILING_COPY_WORDS = {
    "clean",
    "dirty",
    "explicit",
    "ck",
    "cut",
    "main",
    "extended",
    "quickhitter",
    "qh",
    "se",
}
VERSION_KIND_WORDS = {
    "bootleg",
    "edit",
    "flip",
    "mashup",
    "mix",
    "redrum",
    "refix",
    "remix",
    "revibe",
    "rmx",
}
NON_VANILLA_WORDS = VERSION_KIND_WORDS | {
    "acca",
    "acapella",
    "edits",
    "inst",
    "instrumental",
    "se",
    "segue",
    "short",
    "sickmix",
    "trans",
    "transition",
    "transitions",
}
PREFERRED_LABEL_DELIVERY_WORDS = TRAILING_COPY_WORDS | {
    "radio",
    "squeaky",
    "super",
}
TITLE_MODIFIER_ALIASES = {
    "trans": ("trans", "transition", "transitions"),
}
MINIMUM_RESULT_BYTES = 1024 * 1024
SIZE_PATTERN = re.compile(r"^\s*([\d,.]+)\s*(bytes?|[kmgt]i?b)\s*$", flags=re.IGNORECASE)
TRAILING_BPM_PATTERN = re.compile(r"(?<![\d-])(\d{2,3})(?:\s*bpm)?\s*$", flags=re.IGNORECASE)
PARENTHETICAL_BPM_PATTERN = re.compile(
    r"\((?P<label>[^()]*?)(?P<bpm>\d{2,3})\s*bpm\s*\)\s*$",
    flags=re.IGNORECASE,
)
VANILLA_PARENTHETICALS = {
    "clean",
    "dirty",
    "explicit",
    "original",
    "original mix",
}


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
    if hostname == CRATE_SEARCH_HOST and parsed.path == CRATE_SEARCH_PATH:
        parameters = parse_qs(parsed.query)
        if (
            parameters.get("action") in (["stream"], ["download"])
            and len(parameters.get("id", [])) == 1
            and bool(parameters["id"][0])
            and len(parameters.get("key", [])) == 1
            and bool(parameters["key"][0])
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


def parsed_size_bytes(value: object) -> float | None:
    """Parse provider display sizes, returning None when size is unavailable."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value >= 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    match = SIZE_PATTERN.fullmatch(value)
    if not match:
        return None
    try:
        amount = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = match.group(2).casefold()
    exponent = {
        "byte": 0,
        "bytes": 0,
        "kb": 1,
        "kib": 1,
        "mb": 2,
        "mib": 2,
        "gb": 3,
        "gib": 3,
        "tb": 4,
        "tib": 4,
    }.get(unit)
    return amount * (1024**exponent) if exponent is not None else None


def result_meets_minimum_size(item: dict[str, object]) -> bool:
    """Keep unknown sizes, but reject known files smaller than one megabyte."""
    size = parsed_size_bytes(item.get("size"))
    return size is None or size >= MINIMUM_RESULT_BYTES


def split_trailing_bpm(name: str) -> tuple[str, int | None]:
    """Split a plausible terminal BPM from a display name without changing its filename."""
    value = name.strip()
    extension = ""
    suffix = PurePosixPath(value).suffix
    if suffix.lower().lstrip(".") in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS:
        extension = suffix
        value = value[: -len(suffix)].rstrip()

    parenthetical = PARENTHETICAL_BPM_PATTERN.search(value)
    if parenthetical:
        bpm = int(parenthetical.group("bpm"))
        if 40 <= bpm <= 250:
            label = parenthetical.group("label").rstrip(" -–—_")
            replacement = f"({label})" if label else ""
            display = f"{value[:parenthetical.start()].rstrip()} {replacement}".strip()
            return f"{display}{extension}", bpm

    terminal = TRAILING_BPM_PATTERN.search(value)
    if not terminal:
        return name, None
    bpm = int(terminal.group(1))
    if not 40 <= bpm <= 250:
        return name, None
    display = value[:terminal.start()].rstrip(" -–—_")
    # Providers sometimes repeat the same terminal BPM ("126 126").
    duplicate = re.search(rf"(?<!\d){bpm}\s*$", display)
    if duplicate:
        display = display[:duplicate.start()].rstrip(" -–—_")
    return f"{display}{extension}", bpm


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


def clean_catalog_name(name: str) -> str:
    """Remove an audio extension and provider/list track-number prefix."""
    value = name.strip()
    suffix = PurePosixPath(value).suffix.lower().lstrip(".")
    if suffix in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS:
        value = value[: -(len(suffix) + 1)]
    return re.sub(r"^\s*(?:\d{1,3}[_-]\d{1,3}\s*|\d{1,3}\s*[._-]\s*)", "", value)


def preferred_term_rank(name: str) -> int | None:
    """Return the configured label rank; CK must be a standalone word."""
    normalized_name = name.casefold()
    for rank, term in enumerate(RESULT_PRIORITY_TERMS[:-1]):
        if term == "CK":
            if re.search(r"(?<!\w)ck(?!\w)", normalized_name):
                return rank
        elif term.casefold() in normalized_name:
            return rank
    return None


def preferred_label_key(name: str) -> str:
    """Return a promoted label without its trailing delivery description."""
    rank = preferred_term_rank(name)
    if rank is None:
        return ""
    term_tokens = normalized_match_tokens(RESULT_PRIORITY_TERMS[rank], ignore_stopwords=False)
    _artist, title = artist_and_title(clean_catalog_name(name))
    title_tokens = normalized_match_tokens(title, ignore_stopwords=False)
    start = next(
        (
            index
            for index in range(len(title_tokens) - len(term_tokens) + 1)
            if title_tokens[index : index + len(term_tokens)] == term_tokens
        ),
        None,
    )
    label_tokens = title_tokens[start:] if start is not None else term_tokens
    while label_tokens and (
        label_tokens[-1] in PREFERRED_LABEL_DELIVERY_WORDS
        or re.fullmatch(r"\d{2,3}", label_tokens[-1])
    ):
        label_tokens.pop()
    return " ".join(label_tokens or term_tokens)


def is_vanilla_artist(artist: str) -> bool:
    """Reject versus/mashup source lists from otherwise preferred versions."""
    artist_tokens = normalized_match_tokens(artist, ignore_stopwords=False)
    return not (
        re.search(r"(?<!\w)vs\.?(?!\w)", artist, flags=re.IGNORECASE)
        or (artist_tokens and artist_tokens[0] in {"acca", "acapella", "inst", "instrumental"})
        or len(artist_tokens) > 5
    )


def is_tmu_named_version(name: str) -> bool:
    """Keep TMU-branded named versions preferred without promoting TMU copy tags."""
    artist, title = artist_and_title(clean_catalog_name(name))
    if not artist or not is_vanilla_artist(artist):
        return False
    for match in PARENTHETICAL.finditer(title):
        tokens = normalized_match_tokens(match.group(0), ignore_stopwords=False)
        if tokens and tokens[0] == "tmu" and any(token in NON_VANILLA_WORDS for token in tokens):
            return True

    unwrapped = PARENTHETICAL.sub(" ", title)
    tokens = normalized_match_tokens(unwrapped, ignore_stopwords=False)
    try:
        tmu_index = tokens.index("tmu")
    except ValueError:
        return False
    return any(token in NON_VANILLA_WORDS for token in tokens[tmu_index + 1 :])


def is_vanilla_version(name: str) -> bool:
    """Identify plain/original tracks and their Intro/Clean/Dirty copies."""
    value = clean_catalog_name(name)
    artist, separator, title = value.partition("-")
    if not separator or not artist.strip() or not title.strip():
        return False
    if not is_vanilla_artist(artist):
        return False

    for match in PARENTHETICAL.finditer(title):
        tokens = normalized_match_tokens(match.group(0), ignore_stopwords=False)
        normalized = " ".join(tokens)
        if normalized in VANILLA_PARENTHETICALS:
            continue
        if tokens and all(token in VANILLA_DELIVERY_WORDS or token.isdigit() for token in tokens):
            continue
        if preferred_term_rank(match.group(0)) is not None and not any(
            token in NON_VANILLA_WORDS for token in tokens
        ):
            continue
        return False

    unwrapped_title = PARENTHETICAL.sub(" ", title)
    tokens = normalized_match_tokens(unwrapped_title, ignore_stopwords=False)
    while tokens and (
        tokens[-1] in VANILLA_DELIVERY_WORDS
        or re.fullmatch(r"\d{2,3}", tokens[-1])
    ):
        tokens.pop()
    return bool(tokens) and not any(token in NON_VANILLA_WORDS for token in tokens)


def is_labeled_vanilla_version(name: str) -> bool:
    """Return true for vanilla copies labelled Intro/Clean/Dirty/CK."""
    if not is_vanilla_version(name):
        return False
    _artist, title = artist_and_title(clean_catalog_name(name))
    return re.search(r"(?<!\w)(?:intro|clean|dirty|ck)(?!\w)", title, flags=re.IGNORECASE) is not None


def priority_rank(name: str) -> int:
    vanilla = is_vanilla_version(name)
    if vanilla:
        preferred_rank = preferred_term_rank(name)
        if preferred_rank is not None:
            return preferred_rank
    elif is_tmu_named_version(name):
        return 0
    _artist, title = artist_and_title(name)
    if vanilla and is_labeled_vanilla_version(name):
        return INTRO_PRIORITY_RANK
    if vanilla:
        return VANILLA_PRIORITY_RANK
    if re.search(r"(?<!\w)intro(?!\w)", title, flags=re.IGNORECASE):
        return INTRO_VARIANT_PRIORITY_RANK
    return OTHER_PRIORITY_RANK


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


def version_family_key(name: str) -> str:
    """Collapse delivery variants while retaining the named edit/remix identity."""
    value = clean_catalog_name(name)

    # Prefer a named version label over the complete filename. Providers often
    # vary the artist/title around the same version (for example "Dance" vs
    # "Dance With Me"), while the label is the useful grouping identity.
    for match in PARENTHETICAL.finditer(value):
        label_tokens = normalized_match_tokens(match.group(0))
        label_tokens = ["remix" if token == "rmx" else token for token in label_tokens]
        if len(label_tokens) >= 2 and any(token in VERSION_KIND_WORDS for token in label_tokens):
            # "OG To X Remix" describes a transition from the original into X,
            # not a different remix credited to "OG".
            if label_tokens[0] == "og" and len(label_tokens) >= 3:
                label_tokens.pop(0)
            return "version:" + " ".join(label_tokens)

    def remove_copy_qualifier(match: re.Match[str]) -> str:
        tokens = normalized_match_tokens(match.group(0), ignore_stopwords=False)
        normalized = " ".join(tokens)
        if normalized in VERSION_COPY_QUALIFIERS:
            return " "
        if tokens and all(token.isdigit() or token == "bpm" for token in tokens):
            return " "
        return match.group(0)

    value = PARENTHETICAL.sub(remove_copy_qualifier, value)
    # Connector words are immaterial here too, making "&" and "and" copies
    # converge without weakening the search relevance gate.
    tokens = normalized_match_tokens(value)
    # Remove filename delivery labels and one or more trailing BPM values. The
    # meaningful label (for example "Smassh Edit") deliberately remains.
    while tokens and (tokens[-1] in TRAILING_COPY_WORDS or re.fullmatch(r"\d{2,3}", tokens[-1])):
        tokens.pop()
    return " ".join(tokens)


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

    def sort_key(item: dict[str, object]) -> tuple[int, str, int]:
        name = str(item["name"])
        rank = priority_rank(name)
        if rank < INTRO_PRIORITY_RANK:
            return rank, preferred_label_key(name), 0
        if rank != INTRO_PRIORITY_RANK:
            return rank, "", 0
        artist, title = artist_and_title(name)
        artist_key = " ".join(artist.casefold().split())
        return rank, str(bare_intro_rank(title)), artist_groups[artist_key]

    results.sort(key=sort_key)

    # Keep the site's order between version families, but make every family a
    # contiguous block. Preferred labels promote only vanilla copies; when the
    # same label occurs on a named edit/remix, its family stays in the normal
    # version tiers and its delivery copies remain adjacent.
    families: dict[str, list[dict[str, object]]] = {}
    family_order: list[str] = []
    for item in results:
        name = str(item["name"])
        rank = priority_rank(name)
        key = version_family_key(name)
        # Named edits/remixes may span delivery labels, but plain versions are
        # kept in separate rank-scoped families.
        if rank < INTRO_PRIORITY_RANK:
            key = f"preferred-label:{rank}:{preferred_label_key(name)}"
        elif not key.startswith("version:"):
            key = f"rank:{rank}:{key}"
        if key not in families:
            families[key] = []
            family_order.append(key)
        families[key].append(item)
    results[:] = [item for key in family_order for item in families[key]]


def merge_result_models(models: object, *, later_title_term: str = "") -> dict[str, object]:
    """Merge parsed result models without duplicating the same remote media item."""
    if not isinstance(models, list):
        raise ValueError("Result models were not provided as a list.")
    merged: list[dict[str, object]] = []
    seen: set[tuple[str, ...]] = set()
    normalized_later_term = later_title_term.casefold().strip()
    accepted_later_terms = TITLE_MODIFIER_ALIASES.get(
        normalized_later_term,
        (normalized_later_term,),
    )
    later_title_pattern = (
        re.compile(
            rf"(?<!\w)(?:{'|'.join(re.escape(term) for term in accepted_later_terms)})(?!\w)",
            flags=re.IGNORECASE,
        )
        if normalized_later_term
        else None
    )
    for model_index, model in enumerate(models):
        if not isinstance(model, dict) or not isinstance(model.get("results"), list):
            raise ValueError("A merged search result model was invalid.")
        for item in model["results"]:
            if not isinstance(item, dict):
                raise ValueError("A merged search result item was invalid.")
            if not result_meets_minimum_size(item):
                continue
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
            elif provider == "DJFolders" and item.get("provider_item_id"):
                identity = ("crate-track", str(item["provider_item_id"]))
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
        size = size_node.get_text(" ", strip=True) if size_node else ""
        if not result_meets_minimum_size({"size": size}):
            continue
        visible_name, bpm = split_trailing_bpm(name or display_name or "Unnamed result")
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
                "display_name": visible_name,
                "bpm": bpm,
                "filename": display_name or name or "download.bin",
                "size": size,
                "size_bytes": parsed_size_bytes(size),
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
        if not result_meets_minimum_size({"size": size}):
            continue
        track_key = (
            " ".join(name.casefold().split()),
            " ".join(size.casefold().split()),
        )
        if track_key in seen_tracks:
            continue
        seen_tracks.add(track_key)
        preview_url = safe_preview_url(hit.get("stream"))
        download_url = safe_remote_url(hit.get("download"))
        visible_name, bpm = split_trailing_bpm(name)
        results.append(
            {
                "id": f"djpool-rest-{index}",
                "name": name,
                "display_name": visible_name,
                "bpm": bpm,
                "filename": filename,
                "size": size,
                "size_bytes": parsed_size_bytes(size),
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
        size_node = entry.select_one(".entry-info-size")
        size = size_node.get_text(" ", strip=True) if size_node else ""
        if not result_meets_minimum_size({"size": size}):
            continue
        seen_filenames.add(filename_key)
        original_name = name or filename or "Unnamed result"
        visible_name, bpm = split_trailing_bpm(original_name)
        results.append(
            {
                "id": f"rvremix-{entry.get('data-id') or index}",
                "name": original_name,
                "display_name": visible_name,
                "bpm": bpm,
                "filename": filename or name or "download.mp3",
                "size": size,
                "size_bytes": parsed_size_bytes(size),
                "kind": "audio",
                "mime_type": declared_type or "audio/mpeg",
                "preview_url": stream_url,
                "download_url": stream_url,
                "provider": "RVRemix",
            }
        )

    sort_results(results)
    return {"count": len(results), "results": results}
