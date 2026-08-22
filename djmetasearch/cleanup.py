"""Transactional filename and Artist/Title cleanup for completed downloads."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile

SUPPORTED_EXTENSIONS = {".mp3", ".m4a"}
DJPOOL_PLACEHOLDER_COMMENTS = {"djpoolrecords.com", "www.djpoolrecords.com"}
KNOWN_ACRONYMS = ("DJ", "TMU", "CK", "MMP", "QH", "HH")
FEATURE_SEPARATOR = re.compile(r"\s+\b(?:ft|feat|featuring)\.?\s+", flags=re.IGNORECASE)
TRAILING_NUMBER = re.compile(r"^(.*\S)\s+(\d{2,3})$")
TRAILING_CAMELOT_KEY = re.compile(r"^(.*\S)\s+((?:[1-9]|1[0-2])[AB])$", flags=re.IGNORECASE)
SYMBOL_ACRONYM = re.compile(
    r"(?:\b(?:[A-Z]\.){2,}[A-Z]?\.?\b|\b[A-Z]+\$[A-Z]+\b|\b[A-Z]{1,4}(?:/[A-Z]{1,4})+\b)"
)
PARENTHETICAL_SE = re.compile(r"\(\s*se\s*\)", flags=re.IGNORECASE)
INTRO_LABEL = r"(?:(?:dj|tmu|ck|mmp)\s+)?intro"
INTRO_CLEAN_DIRTY = re.compile(
    rf"(?:\(\s*(?:{INTRO_LABEL}(?:\s*-\s*|\s+)(?:clean|dirty)"
    rf"|(?:clean|dirty)(?:\s*-\s*|\s+){INTRO_LABEL})\s*\)"
    rf"|\b(?:{INTRO_LABEL}\s*-\s*(?:clean|dirty)|(?:clean|dirty)\s*-\s*{INTRO_LABEL})\b)",
    flags=re.IGNORECASE,
)
SHORT_EDIT_LABEL = r"short\s+edit"
SHORT_EDIT_CLEAN_DIRTY = re.compile(
    rf"(?:\(\s*(?:{SHORT_EDIT_LABEL}(?:\s*-\s*|\s+)(?:clean|dirty)"
    rf"|(?:clean|dirty)(?:\s*-\s*|\s+){SHORT_EDIT_LABEL})\s*\)"
    rf"|\b(?:{SHORT_EDIT_LABEL}\s*-\s*(?:clean|dirty)"
    rf"|(?:clean|dirty)\s*-\s*{SHORT_EDIT_LABEL})\b)",
    flags=re.IGNORECASE,
)
RELEASE_YEAR = re.compile(r"(?<!\d)(18\d{2}|19\d{2}|20\d{2}|21\d{2})(?!\d)")
YEAR = re.compile(r"^\d{4}$")
FINDER_SCRIPT = """
on run argv
    set targetFile to POSIX file (item 1 of argv) as alias
    set commentText to item 2 of argv
    tell application "Finder"
        set comment of targetFile to commentText
        return comment of targetFile
    end tell
end run
"""


@dataclass(frozen=True)
class CleanupResult:
    original_path: Path
    final_path: Path
    artist: str
    title: str
    changed: bool
    warning: str | None = None
    release_year: str = ""


def normalize_whitespace(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return text.strip(" ,")


def normalize_acronyms(text: str) -> str:
    text = PARENTHETICAL_SE.sub("(SE)", text)
    for acronym in KNOWN_ACRONYMS:
        text = re.sub(rf"\b{re.escape(acronym)}\b", acronym, text, flags=re.IGNORECASE)
    return text


def is_all_upper(text: str) -> bool:
    letters = [character for character in text if character.isalpha()]
    return bool(letters) and all(character.isupper() for character in letters)


def conservative_smart_case(text: str) -> str:
    text = normalize_whitespace(text)
    text = PARENTHETICAL_SE.sub("(SE)", text)
    if not is_all_upper(text):
        return normalize_acronyms(text)
    preserved = [(match.span(), match.group(0)) for match in SYMBOL_ACRONYM.finditer(text)]
    title_cased = text.title()
    title_cased = re.sub(
        r"'(?P<suffix>S|T|D|Ll|Re|Ve|M)\b",
        lambda match: "'" + match.group("suffix").lower(),
        title_cased,
    )
    characters = list(title_cased)
    for (start, end), original in preserved:
        characters[start:end] = original
    return normalize_acronyms("".join(characters))


def clean_artist(value: str) -> str:
    value = value.replace("[", "(").replace("]", ")")
    value = FEATURE_SEPARATOR.sub(", ", value)
    return conservative_smart_case(value)


def remove_trailing_bpm(value: str) -> str:
    value = value.strip()
    while True:
        match = TRAILING_NUMBER.match(value)
        if not match:
            return value
        prefix, number_text = match.groups()
        number = int(number_text)
        if not 50 <= number <= 220 or not prefix.strip():
            return value
        value = prefix.rstrip()


def remove_trailing_camelot_key(value: str) -> str:
    match = TRAILING_CAMELOT_KEY.match(value.strip())
    if not match:
        return value.strip()
    return match.group(1).rstrip()


def remove_trailing_track_markers(value: str) -> str:
    value = value.strip()
    while True:
        cleaned = remove_trailing_camelot_key(remove_trailing_bpm(value))
        if cleaned == value:
            return value
        value = cleaned


def normalize_intro_variant(value: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        variant = "Dirty" if "dirty" in match.group(0).lower() else "Clean"
        prefix_match = re.search(rf"\b({'|'.join(KNOWN_ACRONYMS)})\s+intro\b", match.group(0), flags=re.IGNORECASE)
        intro_label = f"{prefix_match.group(1).upper()} Intro" if prefix_match else "Intro"
        return f"({intro_label}) ({variant})"

    value = INTRO_CLEAN_DIRTY.sub(replacement, value)

    def short_edit_replacement(match: re.Match[str]) -> str:
        variant = "Dirty" if "dirty" in match.group(0).lower() else "Clean"
        return f"(Short Edit) ({variant})"

    return normalize_whitespace(SHORT_EDIT_CLEAN_DIRTY.sub(short_edit_replacement, value))


def clean_title(value: str) -> str:
    value = value.replace("[", "(").replace("]", ")")
    value = remove_trailing_track_markers(normalize_whitespace(value))
    value = conservative_smart_case(value)
    return normalize_intro_variant(value)


def parse_filename_parts(filename: str) -> tuple[str, str]:
    stem = Path(filename).stem
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem.strip()


def safe_filename_component(value: str) -> str:
    value = value.replace("/", " - ").replace(":", " - ").replace("\0", "")
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "Untitled"


def unique_destination(directory: Path, suggested_name: str) -> Path:
    safe_name = Path(suggested_name or "download.bin").name
    candidate = directory / safe_name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    index = 1
    while True:
        alternative = directory / f"{stem} ({index}){suffix}"
        if not alternative.exists():
            return alternative
        index += 1


def first_easy_tag(audio: object, key: str) -> str:
    try:
        values = audio.get(key, [])
    except Exception:
        return ""
    if not values:
        return ""
    return str(values[0]).strip()


def extract_release_year(value: str) -> str:
    match = RELEASE_YEAR.search(value or "")
    return match.group(1) if match else ""


def release_year_from_audio(audio: object) -> str:
    for key in ("date", "originaldate", "year"):
        year = extract_release_year(first_easy_tag(audio, key))
        if year:
            return year
    return ""


def _comment_strings(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)] if value is not None else []


def clear_placeholder_comments(path: Path) -> bool:
    """Clear embedded comments only when their sole content is DJPoolRecords' placeholder."""
    audio = MutagenFile(path, easy=False)
    if audio is None or audio.tags is None:
        return False
    tags = audio.tags
    values: list[str] = []
    delete_comments = None

    if hasattr(tags, "getall") and hasattr(tags, "delall"):
        frames = tags.getall("COMM")
        for frame in frames:
            values.extend(_comment_strings(getattr(frame, "text", [])))
        if frames:
            delete_comments = lambda: tags.delall("COMM")
    elif "\xa9cmt" in tags:
        values.extend(_comment_strings(tags.get("\xa9cmt", [])))
        delete_comments = lambda: tags.__delitem__("\xa9cmt")

    nonempty = [value.strip().casefold() for value in values if value.strip()]
    if not delete_comments or not nonempty or any(value not in DJPOOL_PLACEHOLDER_COMMENTS for value in nonempty):
        return False
    delete_comments()
    audio.save()
    return True


def write_finder_release_year(path: Path, year: str) -> None:
    if not YEAR.fullmatch(year):
        raise ValueError(f"Invalid release year: {year!r}")
    if not path.is_file():
        raise FileNotFoundError(f"Audio file does not exist: {path}")

    comment = year
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", FINDER_SCRIPT, str(path), comment],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or "Finder returned an unknown error"
        raise OSError(f"Could not write the Finder release-year comment: {detail}")
    if result.stdout.strip() != comment:
        raise OSError(
            "Could not verify the Finder release-year comment: "
            f"expected {comment!r}, received {result.stdout.strip()!r}"
        )


def clean_working_audio(path: Path, suggested_name: str) -> tuple[str, str, str, bool, str]:
    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError("Mutagen could not identify the downloaded audio file.")
    if audio.tags is None:
        audio.add_tags()

    fallback_artist, fallback_title = parse_filename_parts(suggested_name)
    original_artist = first_easy_tag(audio, "artist")
    original_title = first_easy_tag(audio, "title")
    release_year = release_year_from_audio(audio)
    artist = clean_artist(original_artist or fallback_artist)
    title = clean_title(original_title or fallback_title)
    if not artist and not title:
        raise ValueError("No Artist or Title could be read from tags or filename.")

    if artist:
        audio["artist"] = [artist]
    if title:
        audio["title"] = [title]
    audio.save()
    comments_cleared = clear_placeholder_comments(path)

    extension = Path(suggested_name).suffix or path.suffix
    if artist and title:
        stem = f"{artist} - {title}"
    else:
        stem = artist or title
    final_name = f"{safe_filename_component(stem)}{extension}"
    changed = (
        artist != original_artist
        or title != original_title
        or final_name != Path(suggested_name).name
        or bool(release_year)
        or comments_cleared
    )
    return artist, title, final_name, changed, release_year


def _copy_to_atomic_temp(source: Path, destination_directory: Path, suffix: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".djpool_cleanup_",
        suffix=suffix,
        dir=destination_directory,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    shutil.copy2(source, temporary)
    return temporary


def process_download(source_path: Path, suggested_name: str, destination_directory: Path) -> CleanupResult:
    """Clean a staged download without ever modifying the original bytes."""
    source_path = source_path.resolve()
    destination_directory = destination_directory.expanduser().resolve()
    destination_directory.mkdir(parents=True, exist_ok=True)
    suggested_name = Path(suggested_name or "download.bin").name
    extension = Path(suggested_name).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        final_path = unique_destination(destination_directory, suggested_name)
        temporary = _copy_to_atomic_temp(source_path, destination_directory, Path(suggested_name).suffix)
        os.replace(temporary, final_path)
        return CleanupResult(source_path, final_path, "", "", False)

    working: Path | None = None
    placed: Path | None = None
    try:
        working = _copy_to_atomic_temp(source_path, destination_directory, Path(suggested_name).suffix)
        artist, title, final_name, changed, release_year = clean_working_audio(working, suggested_name)
        final_path = unique_destination(destination_directory, final_name)
        os.replace(working, final_path)
        working = None
        placed = final_path
        if release_year:
            write_finder_release_year(final_path, release_year)
        placed = None
        return CleanupResult(source_path, final_path, artist, title, changed, release_year=release_year)
    except Exception as exc:
        if working is not None:
            working.unlink(missing_ok=True)
        if placed is not None:
            placed.unlink(missing_ok=True)
        fallback_path = unique_destination(destination_directory, suggested_name)
        fallback = _copy_to_atomic_temp(source_path, destination_directory, Path(suggested_name).suffix)
        os.replace(fallback, fallback_path)
        return CleanupResult(
            source_path,
            fallback_path,
            "",
            "",
            False,
            f"Cleanup failed; saved the untouched download ({exc}).",
        )
