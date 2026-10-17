"""Search JioSaavn's website API and expose canonical song links."""
from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path
import urllib.parse
import urllib.request

from .results import sort_results

BASE_URL = "https://www.jiosaavn.com/api.php"


def safe_song_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme == "https" and parsed.netloc == "www.jiosaavn.com"
            and parsed.path.startswith("/song/") and not parsed.query and not parsed.fragment):
        return value
    return ""


def parse_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("JioSaavn returned an invalid search response.")
    results, seen = [], set()
    for row in payload["results"]:
        if not isinstance(row, dict) or row.get("type") != "song":
            continue
        url = safe_song_url(row.get("perma_url"))
        title = html.unescape(str(row.get("title") or "")).strip()
        if not url or not title or url in seen:
            continue
        info = row.get("more_info") if isinstance(row.get("more_info"), dict) else {}
        artist_map = info.get("artistMap")
        artists = artist_map.get("primary_artists", []) if isinstance(artist_map, dict) else []
        artist = ", ".join(html.unescape(str(a.get("name") or "")) for a in artists if isinstance(a, dict))
        name = f"{artist} - {title}" if artist else title
        seen.add(url)
        results.append({
            "id": f"jiosaavn-{row.get('id') or len(results)}", "name": name,
            "display_name": name, "filename": name, "kind": "audio", "provider": "JioSaavn",
            "artist": artist, "album": html.unescape(str(info.get("album") or "")),
            "year": str(row.get("year") or ""), "size": "", "size_bytes": None,
            "bpm": None, "preview_url": "", "download_url": "", "external_url": url,
        })
    sort_results(results)
    return {"count": len(results), "results": results}


class JioSaavnClient:
    def search(self, query: str) -> dict[str, object]:
        query = " ".join(query.replace(" - ", " ").split())
        if not query:
            return {"count": 0, "results": []}
        parameters = {"__call": "search.getResults", "_format": "json", "_marker": "0",
                      "api_version": "4", "ctx": "web6dot0", "q": query, "p": "1", "n": "50"}
        request = urllib.request.Request(BASE_URL + "?" + urllib.parse.urlencode(parameters),
                                         headers={"User-Agent": "DJMetaSearch/1.0", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return parse_payload(json.load(response))
        except Exception as exc:
            raise RuntimeError(f"JioSaavn search failed: {exc}") from exc


def handoff_to_orpheus(url: str) -> None:
    """Send a validated song link to the existing JioLite/OrpheusDL app."""
    url = safe_song_url(url)
    if not url:
        raise ValueError("Invalid JioSaavn song URL.")
    candidates = [Path.home() / "Applications/JioLite.app", Path("/Applications/JioLite.app")]
    application = next((path for path in candidates if path.is_dir()), None)
    if application is None:
        raise RuntimeError("Install JioLite to hand songs to OrpheusDL.")
    handoff = "jiolite://download?" + urllib.parse.urlencode({"url": url}, quote_via=urllib.parse.quote)
    result = subprocess.run(["/usr/bin/open", "-a", str(application), handoff],
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError("Could not open JioLite for OrpheusDL: " + result.stderr.strip())
