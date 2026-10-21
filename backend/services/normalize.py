"""URL normalization and duplicate fingerprints."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = re.compile(r"^(utm_.*|gh_src|gh_jid_src|source|src|ref|referrer|lever-source.*|trk.*|fbclid|gclid)$", re.I)


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
                   if not _TRACKING_PARAMS.match(k))
    return urlunsplit(("https", host, path, urlencode(query), ""))


def _norm_text(value: str | None) -> str:
    value = (value or "").lower()
    value = re.sub(r"\(.*?\)|[^a-z0-9 ]", " ", value)
    return " ".join(value.split())


def job_fingerprint(company: str, title: str, location: str | None) -> str:
    key = "|".join(_norm_text(x) for x in (company, title, location))
    return hashlib.sha256(key.encode()).hexdigest()[:32]
