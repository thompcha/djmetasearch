"""Public RVRemix LetsBox search client."""

from __future__ import annotations

import http.cookiejar
import json
import re
import unicodedata
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

from .query import strip_apostrophes
from .results import parse_rvremix_payload

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
        match = re.search(r"\bvar\s+LetsBox_vars\s*=\s*(\{.*?\})\s*;", page, re.DOTALL)
        if not match:
            raise RuntimeError("RVRemix did not expose its LetsBox configuration.")
        variables = json.loads(match.group(1))
        module = BeautifulSoup(page, "html.parser").select_one(
            ".wpcp-module.LetsBox.searchlist[data-list='search']"
        )
        if module is None:
            raise RuntimeError("RVRemix audio search widget was not found.")
        self.template = {
            "url": str(variables["ajax_url"]),
            "action": "letsbox-get-filelist",
            "listtoken": str(module.get("data-token") or ""),
            "account_id": str(module.get("data-account-id") or ""),
            "lastFolder": "",
            "folderPath": str(module.get("data-path") or ""),
            "sort": str(module.get("data-sort") or "name:asc"),
            "_ajax_nonce": str(variables["refresh_nonce"]),
            "mobile": "",
        }
        if not self.template["listtoken"] or not self.template["account_id"]:
            raise RuntimeError("RVRemix audio search widget was incomplete.")
        return self.template

    def search(self, query: str) -> dict[str, object]:
        focused_query = quoted_rvremix_query(query)
        if not focused_query:
            return {"count": 0, "results": []}
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                template = self.template or self.bootstrap()
                form = {key: value for key, value in template.items() if key != "url"}
                form["query"] = focused_query
                with self._request(template["url"], urllib.parse.urlencode(form).encode()) as response:
                    payload = json.load(response)
                return filter_rvremix_model(parse_rvremix_payload(payload), query)
            except Exception as exc:
                last_error = exc
                self.template = None
                if attempt == 0:
                    continue
        raise RuntimeError(f"RVRemix search failed: {last_error}")
