"""YouTube helpers.

We only ever store an 11-character video id that matched a strict regex, and
embed it via youtube-nocookie.com. Arbitrary URLs are never put into an iframe.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, quote_plus, urlsplit

import requests
from flask import current_app

log = logging.getLogger(__name__)

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtube-nocookie.com"}


def parse_video_id(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if VIDEO_ID_RE.match(url):
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or parts.hostname not in YOUTUBE_HOSTS:
        return None

    candidate = None
    if parts.hostname == "youtu.be":
        candidate = parts.path.lstrip("/").split("/")[0]
    elif parts.path == "/watch":
        candidate = (parse_qs(parts.query).get("v") or [None])[0]
    else:
        segments = [s for s in parts.path.split("/") if s]
        if len(segments) >= 2 and segments[0] in {"embed", "shorts", "live", "v"}:
            candidate = segments[1]
    return candidate if candidate and VIDEO_ID_RE.match(candidate) else None


def search_url(query: str) -> str:
    return "https://www.youtube.com/results?search_query=" + quote_plus(f"how to do {query} proper form")


def find_video_id(exercise_name: str) -> str | None:
    """Best-effort search via the YouTube Data API (only if YOUTUBE_API_KEY is set)."""
    key = current_app.config.get("YOUTUBE_API_KEY")
    if not key:
        return None
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": f"how to do {exercise_name} proper form",
                "type": "video",
                "videoEmbeddable": "true",
                "safeSearch": "strict",
                "maxResults": 1,
                "key": key,
            },
            timeout=8,
        )
        resp.raise_for_status()
        items = resp.json().get("items") or []
        vid = items[0]["id"]["videoId"] if items else None
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        log.warning("YouTube search failed: %s", type(exc).__name__)
        return None
    return vid if vid and VIDEO_ID_RE.match(vid) else None
