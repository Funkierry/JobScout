"""Shared redaction for private tracker JSON observations and snapshots."""

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_KEY = re.compile(
    r"(?:password|secret|token|auth|cookie|csrf|session|email|phone|mobile|telephone|"
    r"address|passport|idcard|national.?id|candidate.?id|applicant.?id|user.?id|user.?name|api.?key|private.?key|"
    r"record.?id|full.?name|first.?name|last.?name|birth|^id$)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_LONG_NUMBER = re.compile(r"(?<!\d)\d{11,19}(?!\d)")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+")
_PRIVATE_SEGMENT = re.compile(r"(?:\d+|[a-fA-F0-9]{16,}|[a-fA-F0-9-]{32,}|(?=[A-Za-z0-9_-]{20,}$)(?=.*\d)[A-Za-z0-9_-]+)")


def sanitize_url(url: str) -> str:
    """Keep origin and API route shape while removing likely personal URL values."""
    parts = urlsplit(url)
    path = "/".join("[REDACTED]" if _PRIVATE_SEGMENT.fullmatch(segment) else segment for segment in parts.path.split("/"))
    query = urlencode([(key, "[REDACTED]") for key, _ in parse_qsl(parts.query, keep_blank_values=True)])
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def redact_json(value: Any) -> Any:
    """Recursively mask common credential and applicant fields, preserving JSON shape."""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _SENSITIVE_KEY.search(key) else redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, str):
        if value.startswith(("http://", "https://")) and urlsplit(value).hostname:
            value = sanitize_url(value)
        if "@" in value:
            value = _EMAIL.sub("[REDACTED]", value)
        value = _PHONE.sub("[REDACTED]", value)
        value = _LONG_NUMBER.sub("[REDACTED]", value)
        return _BEARER.sub("[REDACTED]", value)
    return value
