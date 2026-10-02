"""Verbatim grounding with whitespace-only tolerance; no semantic inference."""

import re


def ground_excerpt(source: str, candidate: str) -> str | None:
    """Return the original source span, preserving tracker extraction behavior."""
    candidate = candidate.strip()
    if not candidate:
        return ""
    if candidate in source:
        return candidate
    parts = [part for part in re.split(r"\s+", candidate) if part]
    if not parts:
        return ""
    match = re.search(r"\s+".join(re.escape(part) for part in parts), source)
    return match.group(0) if match else None


def is_evidence_grounded(page_text: str, evidence: str, *, allow_empty: bool = False) -> bool:
    if not evidence.strip():
        return allow_empty
    return ground_excerpt(page_text, evidence) is not None
