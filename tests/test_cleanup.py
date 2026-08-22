from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen import File as MutagenFile
from mutagen.id3 import COMM

from djmetasearch import cleanup


def make_audio(
    path: Path,
    artist: str = "",
    title: str = "",
    album: str = "Keep Album",
    release_date: str = "",
    comments: list[str] | None = None,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is required to generate tagged media fixtures")
    codec_args = ["-q:a", "9"] if path.suffix == ".mp3" else ["-c:a", "aac", "-b:a", "64k"]
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "0.1",
            *codec_args,
            "-y",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    audio = MutagenFile(path, easy=True)
    assert audio is not None
    if audio.tags is None:
        audio.add_tags()
    if artist:
        audio["artist"] = [artist]
    if title:
        audio["title"] = [title]
    if release_date:
        audio["date"] = [release_date]
    audio["album"] = [album]
    audio.save()
    if comments:
        raw_audio = MutagenFile(path, easy=False)
        assert raw_audio is not None and raw_audio.tags is not None
        if path.suffix == ".mp3":
            for index, comment in enumerate(comments):
                raw_audio.tags.add(COMM(encoding=3, lang="eng", desc=f"fixture-{index}", text=[comment]))
        else:
            raw_audio.tags["\xa9cmt"] = comments
        raw_audio.save()


def read_embedded_comments(path: Path) -> list[str]:
    audio = MutagenFile(path, easy=False)
    assert audio is not None and audio.tags is not None
    if path.suffix == ".mp3":
        return [str(value) for frame in audio.tags.getall("COMM") for value in frame.text]
    return [str(value) for value in audio.tags.get("\xa9cmt", [])]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("ARTIST FT. GUEST", "Artist, Guest"),
        ("ARTIST FEAT GUEST", "Artist, Guest"),
        ("ARTIST FEATURING GUEST", "Artist, Guest"),
        ("Baby Bash Ft Frankie J", "Baby Bash, Frankie J"),
        ("B.O.B FT. BRUNO MARS", "B.O.B, Bruno Mars"),
        ("A$AP ROCKY FT GUEST", "A$AP Rocky, Guest"),
        ("AKON'S CREW FT. O'NEAL", "Akon's Crew, O'Neal"),
        ("Artist Qh FT. qh Guest", "Artist QH, QH Guest"),
        ("Artist (Se)", "Artist (SE)"),
    ],
)
def test_artist_cleanup(source: str, expected: str) -> None:
    assert cleanup.clean_artist(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("TITLE [DJ INTRO] 100", "Title (DJ Intro)"),
        ("Suga Suga [MMP INTRO] 83 165", "Suga Suga (MMP INTRO)"),
        ("Already Mixed [Tmu Intro] 82", "Already Mixed (TMU Intro)"),
        ("TITLE INTRO - CLEAN 100", "Title (Intro) (Clean)"),
        ("TITLE CLEAN - INTRO 100", "Title (Intro) (Clean)"),
        ("TITLE INTRO - DIRTY 100", "Title (Intro) (Dirty)"),
        ("TITLE DIRTY - INTRO 100", "Title (Intro) (Dirty)"),
        ("TITLE [INTRO - CLEAN] 100", "Title (Intro) (Clean)"),
        ("Mixed Case [Dirty - Intro] 100", "Mixed Case (Intro) (Dirty)"),
        ("TITLE (INTRO CLEAN) 100", "Title (Intro) (Clean)"),
        ("TITLE (CLEAN INTRO) 100", "Title (Intro) (Clean)"),
        ("TITLE (INTRO DIRTY) 100", "Title (Intro) (Dirty)"),
        ("TITLE (DIRTY INTRO) 100", "Title (Intro) (Dirty)"),
        ("Mixed Case [intro clean] 100", "Mixed Case (Intro) (Clean)"),
        (
            "I Wanna Love You (Ck Intro - Clean) 100",
            "I Wanna Love You (CK Intro) (Clean)",
        ),
        ("TITLE (DIRTY - DJ INTRO) 100", "Title (DJ Intro) (Dirty)"),
        ("TITLE [TMU INTRO CLEAN] 100", "Title (TMU Intro) (Clean)"),
        ("TITLE MMP INTRO - DIRTY 100", "Title (MMP Intro) (Dirty)"),
        ("Mixed Qh Edit 100", "Mixed QH Edit"),
        ("Mixed qh Edit 100", "Mixed QH Edit"),
        ("Mixed Hh Edit 100", "Mixed HH Edit"),
        ("Mixed hh Edit 100", "Mixed HH Edit"),
        ("Mixed Case (Se) Edit", "Mixed Case (SE) Edit"),
        ("TITLE (se) EDIT", "Title (SE) Edit"),
        ("TITLE (CLEAN - SHORT EDIT) 100", "Title (Short Edit) (Clean)"),
        ("TITLE (SHORT EDIT - CLEAN) 100", "Title (Short Edit) (Clean)"),
        ("TITLE (DIRTY - SHORT EDIT) 100", "Title (Short Edit) (Dirty)"),
        ("TITLE (SHORT EDIT - DIRTY) 100", "Title (Short Edit) (Dirty)"),
        ("TITLE [CLEAN SHORT EDIT] 100", "Title (Short Edit) (Clean)"),
        ("TITLE SHORT EDIT - DIRTY 100", "Title (Short Edit) (Dirty)"),
        ("TITLE 7A", "Title"),
        ("TITLE 12b", "Title"),
        ("TITLE 7A 100", "Title"),
        ("TITLE 100 7A", "Title"),
        ("TITLE 13A", "Title 13A"),
        ("TITLE 7C", "Title 7C"),
        ("7A", "7A"),
        ("Summer 2024", "Summer 2024"),
        ("Route 40", "Route 40"),
        ("Version 221", "Version 221"),
    ],
)
def test_title_cleanup(source: str, expected: str) -> None:
    assert cleanup.clean_title(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("2006", "2006"),
        ("2006-11-14", "2006"),
        ("Released 1999", "1999"),
        ("Track 100", ""),
        ("20240", ""),
        ("", ""),
    ],
)
def test_release_year_extraction(source: str, expected: str) -> None:
    assert cleanup.extract_release_year(source) == expected


@pytest.mark.parametrize("extension", [".mp3", ".m4a"])
def test_tag_cleanup_and_filename_rebuild_are_transactional(tmp_path: Path, extension: str) -> None:
    source = tmp_path / f"staged{extension}"
    make_audio(
        source,
        "AKON FT. SNOOP DOGG",
        "I Wanna Love You (Ck Intro - Clean) (Clean - Short Edit) (Qh) (Hh) (Se) 7A 100",
        release_date="2006-11-14",
        comments=["  WWW.DJPOOLRECORDS.COM  "],
    )
    original_bytes = source.read_bytes()
    destination = tmp_path / "downloads"

    result = cleanup.process_download(source, f"raw download{extension}", destination)

    assert result.final_path.name == f"Akon, Snoop Dogg - I Wanna Love You (CK Intro) (Clean) (Short Edit) (Clean) (QH) (HH) (SE){extension}"
    assert result.artist == "Akon, Snoop Dogg"
    assert result.title == "I Wanna Love You (CK Intro) (Clean) (Short Edit) (Clean) (QH) (HH) (SE)"
    assert result.changed
    assert result.warning is None
    assert result.release_year == "2006"
    assert source.read_bytes() == original_bytes
    audio = MutagenFile(result.final_path, easy=True)
    assert audio["artist"] == ["Akon, Snoop Dogg"]
    assert audio["title"] == ["I Wanna Love You (CK Intro) (Clean) (Short Edit) (Clean) (QH) (HH) (SE)"]
    assert audio["album"] == ["Keep Album"]
    assert audio["date"] == ["2006-11-14"]
    assert read_embedded_comments(result.final_path) == []
    finder_comment = subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            """
on run argv
    tell application "Finder"
        return comment of (POSIX file (item 1 of argv) as alias)
    end tell
end run
""",
            str(result.final_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert finder_comment.strip() == "2006"


def test_missing_tags_fall_back_to_filename_parts(tmp_path: Path) -> None:
    source = tmp_path / "staged.mp3"
    make_audio(source)
    result = cleanup.process_download(
        source,
        "ARTIST FT GUEST - TITLE [CK INTRO] 105.mp3",
        tmp_path / "downloads",
    )
    assert result.final_path.name == "Artist, Guest - Title (CK Intro).mp3"
    audio = MutagenFile(result.final_path, easy=True)
    assert audio["artist"] == ["Artist, Guest"]
    assert audio["title"] == ["Title (CK Intro)"]


@pytest.mark.parametrize("extension", [".mp3", ".m4a"])
def test_real_comment_is_preserved_even_with_placeholder_value(tmp_path: Path, extension: str) -> None:
    source = tmp_path / f"staged{extension}"
    comments = ["www.djpoolrecords.com", "Keep this real comment"]
    make_audio(source, "Artist", "Title", comments=comments)

    result = cleanup.process_download(source, f"Artist - Title{extension}", tmp_path / "downloads")

    assert result.warning is None
    assert read_embedded_comments(result.final_path) == comments


@pytest.mark.parametrize("extension", [".mp3", ".m4a"])
def test_placeholder_comment_without_www_is_cleared_case_insensitively(tmp_path: Path, extension: str) -> None:
    source = tmp_path / f"staged{extension}"
    make_audio(source, "Artist", "Title", comments=["  DjPoolRecords.Com  "])

    result = cleanup.process_download(source, f"Artist - Title{extension}", tmp_path / "downloads")

    assert result.warning is None
    assert read_embedded_comments(result.final_path) == []


def test_filename_sanitization_and_collision_suffix(tmp_path: Path) -> None:
    source = tmp_path / "staged.mp3"
    make_audio(source, "AC/DC", "Song: Remix 100")
    destination = tmp_path / "downloads"
    destination.mkdir()
    (destination / "AC - DC - Song - Remix.mp3").write_bytes(b"existing")
    result = cleanup.process_download(source, "raw.mp3", destination)
    assert result.final_path.name == "AC - DC - Song - Remix (1).mp3"


def test_unsupported_format_is_copied_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "staged.wav"
    source.write_bytes(b"untouched-wave-bytes")
    result = cleanup.process_download(source, "ARTIST - TITLE 100.wav", tmp_path / "downloads")
    assert result.final_path.name == "ARTIST - TITLE 100.wav"
    assert result.final_path.read_bytes() == source.read_bytes()
    assert not result.changed
    assert result.warning is None


def test_cleanup_failure_saves_original_bytes(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "staged.mp3"
    source.write_bytes(b"original-download-bytes")
    monkeypatch.setattr(cleanup, "clean_working_audio", lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")))
    result = cleanup.process_download(source, "Original Name.mp3", tmp_path / "downloads")
    assert result.final_path.name == "Original Name.mp3"
    assert result.final_path.read_bytes() == b"original-download-bytes"
    assert result.warning and "boom" in result.warning


def test_finder_comment_failure_saves_original_bytes(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "staged.mp3"
    make_audio(source, "Artist", "Title", release_date="2006")
    original_bytes = source.read_bytes()
    monkeypatch.setattr(
        cleanup,
        "write_finder_release_year",
        lambda *_args: (_ for _ in ()).throw(OSError("Finder unavailable")),
    )

    result = cleanup.process_download(source, "Original Name.mp3", tmp_path / "downloads")

    assert result.final_path.name == "Original Name.mp3"
    assert result.final_path.read_bytes() == original_bytes
    assert result.warning and "Finder unavailable" in result.warning
    assert not (tmp_path / "downloads" / "Artist - Title.mp3").exists()


def test_noop_cleanup_reports_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "staged.mp3"
    make_audio(source, "Artist", "Title")
    result = cleanup.process_download(source, "Artist - Title.mp3", tmp_path / "downloads")
    assert result.final_path.name == "Artist - Title.mp3"
    assert not result.changed
