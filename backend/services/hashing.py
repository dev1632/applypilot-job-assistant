from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def hash_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_snapshot(snapshot: dict[str, Any]) -> str:
    """Stable hash of a completed form snapshot (field -> value)."""
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
