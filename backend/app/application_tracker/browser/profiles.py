"""Privacy-preserving persistent-profile path derivation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def profile_directory(root: Path, *, user_id: str, url: str) -> Path:
    """Return a stable per-user/per-host directory without leaking identifiers."""
    if not user_id:
        raise ValueError("user_id must not be empty")
    hostname = urlsplit(url).hostname
    if not hostname:
        raise ValueError("url must contain a hostname")
    return root / f"user-{_digest(user_id)}" / f"site-{_digest(hostname.lower())}"
