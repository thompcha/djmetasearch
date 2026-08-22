from pathlib import Path

from djmetasearch.cleanup import CleanupResult
import standalone_app


class FakeDownload:
    suggested_filename = "SERVER NAME.mp3"

    def __init__(self, path: Path) -> None:
        self._path = path

    def path(self) -> str:
        return str(self._path)


def test_completion_is_built_after_cleanup_and_final_placement(monkeypatch, tmp_path: Path) -> None:
    staged = tmp_path / "staged.mp3"
    staged.write_bytes(b"audio")
    final_path = tmp_path / "downloads" / "Artist - Title.mp3"
    calls: list[tuple[Path, str, Path]] = []

    def fake_process(source: Path, suggested: str, destination: Path) -> CleanupResult:
        calls.append((source, suggested, destination))
        destination.mkdir()
        final_path.write_bytes(b"cleaned")
        return CleanupResult(source, final_path, "Artist", "Title", True)

    monkeypatch.setattr(standalone_app, "process_download", fake_process)
    message, warning = standalone_app.complete_download(FakeDownload(staged), tmp_path / "downloads")

    assert calls == [(staged, "SERVER NAME.mp3", tmp_path / "downloads")]
    assert final_path.exists()
    assert message == "Downloaded and cleaned: Artist - Title.mp3"
    assert warning is False


def test_cleanup_warning_reports_untouched_filename(monkeypatch, tmp_path: Path) -> None:
    staged = tmp_path / "staged.mp3"
    staged.write_bytes(b"audio")
    final_path = tmp_path / "downloads" / "SERVER NAME.mp3"

    def fake_process(source: Path, suggested: str, destination: Path) -> CleanupResult:
        return CleanupResult(source, final_path, "", "", False, "Cleanup failed; saved untouched.")

    monkeypatch.setattr(standalone_app, "process_download", fake_process)
    message, warning = standalone_app.complete_download(FakeDownload(staged), tmp_path / "downloads")

    assert message == "Downloaded unchanged: SERVER NAME.mp3. Cleanup failed; saved untouched."
    assert warning is True


def test_cached_download_filename_adds_extension_from_media_type() -> None:
    assert standalone_app.cached_download_filename("Artist - Title", "audio/mpeg", "audio") == "Artist - Title.mp3"
    assert standalone_app.cached_download_filename("Artist - Title", "audio/mp4", "audio") == "Artist - Title.m4a"
    assert standalone_app.cached_download_filename("Artist - Title", "video/mp4", "video") == "Artist - Title.mp4"
    assert standalone_app.cached_download_filename("Artist - Title.mp3", "video/mp4", "video") == "Artist - Title.mp3"


def test_cached_preview_is_copied_through_cleanup_without_consuming_cache(monkeypatch, tmp_path: Path) -> None:
    cached = tmp_path / "preview-token"
    cached.write_bytes(b"cached audio")
    downloads = tmp_path / "downloads"
    final_path = downloads / "Artist - Title.mp3"
    calls: list[tuple[Path, str, Path]] = []

    def fake_process(source: Path, suggested: str, destination: Path) -> CleanupResult:
        calls.append((source, suggested, destination))
        destination.mkdir()
        final_path.write_bytes(source.read_bytes())
        return CleanupResult(source, final_path, "Artist", "Title", True)

    monkeypatch.setattr(standalone_app, "process_download", fake_process)
    message, warning = standalone_app.complete_cached_preview_download(
        {"path": cached, "mime_type": "audio/mpeg", "kind": "audio"},
        "Artist - Title",
        downloads,
    )

    assert calls == [(cached, "Artist - Title.mp3", downloads)]
    assert cached.read_bytes() == b"cached audio"
    assert final_path.read_bytes() == b"cached audio"
    assert message == "Downloaded and cleaned: Artist - Title.mp3"
    assert warning is False
