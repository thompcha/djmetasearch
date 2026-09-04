"""Private-key client for the Sync.com-backed DJFolders provider."""

from __future__ import annotations

import json
import mimetypes
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import PurePosixPath

from .query import strip_apostrophes
from .results import (
    CRATE_SEARCH_HOST,
    CRATE_SEARCH_PATH,
    filter_djpool_model,
    media_kind,
    parsed_size_bytes,
    result_meets_minimum_size,
    sort_results,
    split_trailing_bpm,
)

BASE_URL = f"https://{CRATE_SEARCH_HOST}{CRATE_SEARCH_PATH}"
USER_AGENT = "DJMetaSearch/1.0"
PAGE_SIZE = 500
MAX_RESULTS = 10_000


def formatted_size(size: int) -> str:
    """Format an exact byte count compactly for the shared result UI."""
    if size < 1024:
        return f"{size} B"
    for unit, divisor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size >= divisor:
            amount = size / divisor
            precision = 0 if amount >= 10 or amount.is_integer() else 1
            return f"{amount:.{precision}f} {unit}"
    return f"{size} B"


def safe_media_reference(reference: object, *, action: str, track_id: str, api_key: str) -> str:
    """Resolve and constrain a service media reference, adding the private key."""
    if not isinstance(reference, str) or action not in {"stream", "download"}:
        return ""
    absolute = urllib.parse.urljoin(BASE_URL, reference)
    parsed = urllib.parse.urlsplit(absolute)
    parameters = urllib.parse.parse_qs(parsed.query)
    if (
        parsed.scheme != "https"
        or parsed.hostname != CRATE_SEARCH_HOST
        or parsed.path != CRATE_SEARCH_PATH
        or parameters.get("action") != [action]
        or parameters.get("id") != [track_id]
    ):
        return ""
    parameters["key"] = [api_key]
    query = urllib.parse.urlencode([(key, value) for key, values in parameters.items() for value in values])
    return urllib.parse.urlunsplit(("https", CRATE_SEARCH_HOST, CRATE_SEARCH_PATH, query, ""))


class CrateSearchClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: float = 25.0) -> None:
        if not api_key:
            raise ValueError("DJFolders API key is missing.")
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.opener = urllib.request.build_opener()

    def _request_json(self, parameters: dict[str, object]) -> dict[str, object]:
        query = urllib.parse.urlencode(parameters)
        url = f"{self.base_url}?{query}"
        last_error: Exception | None = None
        for attempt in range(3):
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                    "X-API-Key": self.api_key,
                },
            )
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise RuntimeError("DJFolders returned invalid JSON.")
                return payload
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code < 500 or attempt == 2:
                    raise RuntimeError(f"DJFolders returned HTTP {exc.code}.") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == 2:
                    break
            time.sleep((0.2 * (2**attempt)) + random.uniform(0, 0.1))
        raise RuntimeError(f"DJFolders is unavailable: {last_error}")

    def search(self, query: str) -> dict[str, object]:
        clean_query = strip_apostrophes(query).strip()
        if not clean_query:
            return {"count": 0, "results": []}
        offset = 0
        rows: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        while offset < MAX_RESULTS:
            payload = self._request_json({
                "q": clean_query,
                "type": "audio",
                "limit": PAGE_SIZE,
                "offset": offset,
            })
            raw_results = payload.get("results")
            if not isinstance(raw_results, list):
                raise RuntimeError("DJFolders response did not contain results.")
            for entry in raw_results:
                if not isinstance(entry, dict) or entry.get("type") != "audio":
                    continue
                track_id = str(entry.get("id") or "")
                filename = str(entry.get("name") or "").strip()
                if not track_id or not filename or track_id in seen_ids or media_kind(filename) != "audio":
                    continue
                raw_size = entry.get("size")
                size_bytes = int(raw_size) if isinstance(raw_size, (int, float)) and raw_size >= 0 else None
                size = formatted_size(size_bytes) if size_bytes is not None else ""
                if not result_meets_minimum_size({"size": size_bytes}):
                    continue
                seen_ids.add(track_id)
                visible_name, bpm = split_trailing_bpm(filename)
                if PurePosixPath(visible_name).suffix.lower().lstrip("."):
                    visible_name = str(PurePosixPath(visible_name).with_suffix(""))
                mime_type = mimetypes.guess_type(filename)[0] or "audio/mpeg"
                rows.append({
                    "id": f"crate-{track_id}",
                    "provider_item_id": track_id,
                    "name": filename,
                    "display_name": visible_name,
                    "filename": filename,
                    "size": size,
                    "size_bytes": size_bytes if size_bytes is not None else parsed_size_bytes(size),
                    "bpm": bpm,
                    "kind": "audio",
                    "mime_type": mime_type,
                    "preview_url": "",
                    "download_url": "",
                    "resolvable": True,
                    "folder": str(entry.get("folder") or ""),
                    "path": str(entry.get("path") or ""),
                    "modified": entry.get("modified"),
                    "provider": "DJFolders",
                })
            count = int(payload.get("count") or len(raw_results))
            total = int(payload.get("total") or 0)
            if count <= 0 or offset + count >= total:
                break
            offset += count
        filtered = filter_djpool_model({"count": len(rows), "results": rows}, clean_query)
        sort_results(filtered["results"])
        return filtered

    def resolve(self, track_id: str) -> dict[str, object]:
        safe_id = str(track_id or "")
        if not safe_id:
            raise ValueError("DJFolders track ID is missing.")
        payload = self._request_json({"action": "resolve", "id": safe_id})
        if str(payload.get("id") or "") != safe_id:
            raise RuntimeError("DJFolders resolved a different track.")
        stream_url = safe_media_reference(
            payload.get("stream"), action="stream", track_id=safe_id, api_key=self.api_key
        )
        download_url = safe_media_reference(
            payload.get("download"), action="download", track_id=safe_id, api_key=self.api_key
        )
        if not stream_url or not download_url:
            raise RuntimeError("DJFolders returned unsafe media references.")
        return {
            "preview_url": stream_url,
            "download_url": download_url,
            "mime_type": str(payload.get("mimeType") or "audio/mpeg"),
            "direct_stream": True,
        }
