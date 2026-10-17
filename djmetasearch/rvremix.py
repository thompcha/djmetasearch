"""Public RVRemix search client."""

from __future__ import annotations

import http.cookiejar
import json
import mimetypes
import re
import unicodedata
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

from .query import strip_apostrophes
from .results import (
    media_kind,
    parsed_size_bytes,
    result_meets_minimum_size,
    safe_preview_url,
    sort_results,
    split_trailing_bpm,
)

PAGE_URL = "https://rvremix.com/audio/"
USER_AGENT = "DJMetaSearch/1.0"
MATCH_STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "with",
}


def normalized_tokens(value: str, *, ignore_stopwords: bool = True) -> list[str]:
    """Create case/diacritic/punctuation-insensitive tokens for local matching."""
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    without_marks = "".join(character for character in decomposed if not unicodedata.combining(character))
    tokens = re.findall(r"[a-z0-9]+", without_marks)
    if ignore_stopwords:
        meaningful = [token for token in tokens if token not in MATCH_STOPWORDS]
        return meaningful or tokens
    return tokens


def rvremix_query_groups(query: str) -> tuple[list[str], list[str]]:
    artist, separator, title = query.strip().partition(" - ")
    if not separator:
        return normalized_tokens(artist), []
    return normalized_tokens(artist), normalized_tokens(title)


def filter_rvremix_model(model: dict[str, object], query: str) -> dict[str, object]:
    """Require both visible artist and title terms in every RVRemix filename."""
    raw_results = model.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("RVRemix result model was invalid.")
    artist_tokens, title_tokens = rvremix_query_groups(query)
    if not artist_tokens and not title_tokens:
        return {"count": 0, "results": []}
    filtered: list[dict[str, object]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        visible_token_list = normalized_tokens(
            str(item.get("filename") or item.get("name") or ""), ignore_stopwords=False
        )
        visible_tokens = set(visible_token_list)
        visible_compact = "".join(visible_token_list)

        def group_matches(required_tokens: list[str]) -> bool:
            if not required_tokens:
                return True
            return (
                all(token in visible_tokens for token in required_tokens)
                or "".join(required_tokens) in visible_compact
            )

        if group_matches(artist_tokens) and group_matches(title_tokens):
            filtered.append(item)
    return {"count": len(filtered), "results": filtered}


def quoted_rvremix_query(query: str) -> str:
    """Turn ``Artist - Title`` into the focused query syntax used by LetsBox."""
    query = strip_apostrophes(query)
    artist, separator, title = query.strip().partition(" - ")
    parts = (artist, title) if separator else (artist,)
    quoted: list[str] = []
    for part in parts:
        clean = re.sub(r"\s+", " ", part.replace('"', " ")).strip()
        if clean:
            quoted.append(f'"{clean}"')
    return " ".join(quoted)


def parse_search_payload(payload: object, endpoint: str, nonce: str) -> dict[str, object]:
    """Convert the site's rvrs_search JSON into the shared audio model."""
    if not isinstance(payload, dict) or payload.get("success") is not True:
        data = payload.get("data") if isinstance(payload, dict) else None
        message = data.get("message") if isinstance(data, dict) else None
        raise ValueError(str(message or "RVRemix search returned an invalid response."))
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("RVRemix search response did not contain items.")
    results = []
    seen = set()
    for entry in data["items"]:
        if not isinstance(entry, dict) or entry.get("type") != "audio":
            continue
        name, file_id = entry.get("name"), entry.get("file_id")
        if not isinstance(name, str) or not name.strip() or not isinstance(file_id, str) or not file_id.isdecimal():
            continue
        if media_kind(name) != "audio":
            continue
        key = " ".join(name.casefold().split())
        size = parsed_size_bytes(entry.get("size"))
        if key in seen or not result_meets_minimum_size({"size": size}):
            continue
        stream = safe_preview_url(endpoint + "?" + urllib.parse.urlencode(
            {"action": "rvrs_stream", "nonce": nonce, "file": file_id}
        ))
        if not stream:
            raise ValueError("RVRemix exposed an unsafe stream endpoint.")
        seen.add(key)
        display_name, bpm = split_trailing_bpm(name)
        results.append({
            "id": f"rvremix-{file_id}", "name": name, "display_name": display_name,
            "bpm": bpm, "filename": name, "size": f"{size / 1024**2:.1f} MB" if size is not None else "",
            "size_bytes": size, "kind": "audio",
            "mime_type": mimetypes.guess_type(name)[0] or "audio/mpeg",
            "preview_url": stream,
            # Download the same audio used by the preview through the shared cache.
            "download_url": stream, "provider": "RVRemix",
        })
    sort_results(results)
    return {"count": len(results), "results": results}


class RVRemixClient:
    def __init__(self, page_url: str = PAGE_URL, timeout: float = 90.0) -> None:
        self.page_url = page_url
        self.timeout = timeout
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.template: dict[str, str] | None = None

    def _request(self, url: str, data: bytes | None = None):
        request = urllib.request.Request(
            url,
            data=data,
            method="POST" if data is not None else "GET",
            headers={
                "User-Agent": USER_AGENT,
                "Referer": self.page_url,
                "Accept": "application/json, text/javascript, */*; q=0.01" if data else "text/html,*/*;q=0.8",
                "X-Requested-With": "XMLHttpRequest" if data else "",
            },
        )
        return self.opener.open(request, timeout=self.timeout)

    def bootstrap(self) -> dict[str, str]:
        with self._request(self.page_url) as response:
            page = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        soup = BeautifulSoup(page, "html.parser")
        for script in soup.find_all("script", src=False):
            source = script.string or script.get_text()
            if not re.search(r"action\s*:\s*[\"']rvrs_search[\"']", source):
                continue
            match = re.search(r'const\s+endpoint\s*=\s*("(?:[^"\\]|\\.)*")\s*,\s*nonce\s*=\s*("(?:[^"\\]|\\.)*")', source)
            if not match:
                continue
            endpoint, nonce = map(json.loads, match.groups())
            if endpoint != "https://rvremix.com/wp-admin/admin-ajax.php" or not nonce:
                raise RuntimeError("RVRemix search configuration was invalid.")
            self.template = {"url": endpoint, "nonce": nonce}
            return self.template
        raise RuntimeError("RVRemix search widget was not found.")

    def search(self, query: str) -> dict[str, object]:
        focused_query = " ".join(query.split())
        if not focused_query:
            return {"count": 0, "results": []}
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                template = self.template or self.bootstrap()
                form = {"action": "rvrs_search", "nonce": template["nonce"],
                        "q": focused_query, "kind": "audio"}
                with self._request(template["url"], urllib.parse.urlencode(form).encode()) as response:
                    payload = json.load(response)
                return filter_rvremix_model(parse_search_payload(payload, template["url"], template["nonce"]), query)
            except Exception as exc:
                last_error = exc
                self.template = None
                if attempt == 0:
                    continue
        raise RuntimeError(f"RVRemix search failed: {last_error}")


def probe_stream_size(url: str, timeout: float = 10.0) -> int | None:
    """Read audio size without transferring the stream; range fallback is one byte."""
    if not safe_preview_url(url) or urllib.parse.urlsplit(url).hostname != "rvremix.com":
        return None
    for method in ("HEAD", "GET"):
        headers = {"Referer": PAGE_URL, "User-Agent": USER_AGENT}
        if method == "GET":
            headers["Range"] = "bytes=0-0"
        try:
            request = urllib.request.Request(url, method=method, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if not response.headers.get("Content-Type", "").lower().startswith("audio/"):
                    return None
                if method == "HEAD":
                    length = response.headers.get("Content-Length", "")
                else:
                    match = re.fullmatch(r"bytes 0-0/(\d+)", response.headers.get("Content-Range", ""))
                    length = match.group(1) if response.status == 206 and match else ""
                if length.isdecimal() and int(length) > 0:
                    return int(length)
        except urllib.error.HTTPError as exc:
            if method != "HEAD" or exc.code not in {405, 501}:
                return None
        except Exception:
            return None
    return None


class RVRemixSizeLookup:
    """Keep at most two probes running; never queue probes for obsolete results."""

    def __init__(self, probe=probe_stream_size):
        from concurrent.futures import ThreadPoolExecutor
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rvremix-size")
        self.probe = probe
        self.wanted = {}
        self.pending = {}
        self.cache = {}
        self.attempted = set()
        self.delivered = {}

    def set_results(self, rows):
        wanted = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity, url = row.get("id"), row.get("preview_url")
            if (isinstance(identity, str) and identity.startswith("rvremix-")
                    and safe_preview_url(url) and urllib.parse.urlsplit(url).hostname == "rvremix.com"):
                wanted[identity] = url
                size = row.get("size_bytes")
                if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
                    self.cache[identity] = int(size)
        self.wanted = wanted

    def poll(self, media_sizes=None):
        for identity, future in list(self.pending.items()):
            if future.done():
                del self.pending[identity]
                try:
                    size = future.result()
                    if size:
                        self.cache[identity] = size
                except Exception:
                    pass
        for identity, url in self.wanted.items():
            if media_sizes and media_sizes.get(url):
                self.cache[identity] = media_sizes[url]
            if identity not in self.cache and identity not in self.attempted and len(self.pending) < 2:
                self.attempted.add(identity)
                self.pending[identity] = self.executor.submit(self.probe, url)
        updates = {identity: self.cache[identity] for identity in self.wanted
                   if identity in self.cache and self.delivered.get(identity) != self.cache[identity]}
        self.delivered.update(updates)
        return updates

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
