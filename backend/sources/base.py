"""Source adapter interface. Adapters only use public, documented job-board endpoints."""
from __future__ import annotations

import html
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import httpx

from backend.config import get_settings

USER_AGENT = "ApplyPilot/0.1 (personal job search assistant; low-volume)"


@dataclass
class RawPosting:
    source: str
    source_job_id: str | None
    company: str
    title: str
    location: str | None
    employment_type: str | None
    url: str
    requisition_id: str | None
    description: str
    posted_at: datetime | None
    # VERIFIED: source states the publication time. ESTIMATED: a creation timestamp that
    # approximates publication. UNVERIFIED: no reliable publication-date evidence.
    freshness_confidence: str


class SourceAdapter(ABC):
    name: str

    def __init__(self, board: str, company: str | None = None, client: httpx.Client | None = None):
        self.board = board
        self.company = company or board
        self._client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30,
                                              follow_redirects=True)
        self._last_request = 0.0

    def _get_json(self, url: str, **params):
        wait = get_settings().applypilot_http_delay_seconds - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    @abstractmethod
    def fetch(self) -> list[RawPosting]: ...


def html_to_text(raw: str | None) -> str:
    text = html.unescape(raw or "")  # Greenhouse double-escapes content
    text = re.sub(r"<(br|/p|/li|/h\d|/div)\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()
