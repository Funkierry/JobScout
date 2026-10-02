"""Bounded current-run source capture and deterministic evidence validation.

Grounding proves quotation provenance, not that an interpretation follows from
the quotation. Public source content remains untrusted data throughout.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from threading import RLock

from app.evidence.grounding import ground_excerpt

from .links import _text, collect_seen_urls, normalize_url

SECTIONS = frozenset({"business", "news", "scale", "required", "preferred", "duties", "stack", "technical", "behavioral", "gap"})
MAX_PAYLOAD = 100_000
MAX_ITEMS = 100
FENCE = chr(96) * 3


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def parse_payload(text: str, fence: str):
    """Exactly one bounded JSON sidecar; never trust surrounding Markdown."""
    if not isinstance(text, str) or len(text) > MAX_PAYLOAD:
        return None
    blocks = re.findall("^" + FENCE + re.escape(fence) + r"[ \t]*\r?\n(.*?)^" + FENCE + r"[ \t]*$", text, re.M | re.S)
    if len(blocks) != 1:
        return None
    try:
        return json.loads(blocks[0], object_pairs_hook=_unique_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (ValueError, TypeError, RecursionError):
        return None


def _string(value, limit):
    return isinstance(value, str) and 0 < len(value.strip()) <= limit


def _previous_year(today):
    try:
        return today.replace(year=today.year - 1)
    except ValueError:  # Feb 29 -> Feb 28, twelve calendar months.
        return today.replace(year=today.year - 1, day=28)


def _date_in_quote(published, text):
    patterns = (
        rf"(?<!\d){published.year}-{published.month:02d}-{published.day:02d}(?!\d)",
        rf"(?<!\d){published.year}[/.]0?{published.month}[/.]0?{published.day}(?!\d)",
        rf"(?<!\d){published.year}\s*年\s*0?{published.month}\s*月\s*0?{published.day}\s*日",
    )
    return any(re.search(pattern, text) for pattern in patterns)


class ResearchEvidence:
    """Content is ephemeral, per-run, bounded, never added to audit events."""

    def __init__(self, *, as_of: date | None = None, mode="interview_prep", records=None, resumes=None, base_bounds=None):
        self.as_of = as_of or date.today()
        self.mode = mode
        self.sources: dict[str, list[tuple[str, str]]] = {}
        self.items: list[dict] = []
        self.rejected: Counter = Counter()
        self.records = records or []
        self.base_bounds = base_bounds or {}
        self.resumes = resumes or {}
        self.read_resumes: dict[str, str] = {}
        self._chars = 0
        self._lock = RLock()

    @property
    def urls(self):
        with self._lock:
            return set(self.sources)

    def observe(self, call, content, status="success"):
        with self._lock:
            text = _text(content)
            pair = [{"type": "ai", "tool_calls": [{**call, "id": "observed"}]}, {"type": "tool", "name": call.get("name"), "tool_call_id": "observed", "status": status, "content": text}]
            urls = collect_seen_urls(pair)
            if not urls:
                return
            if call.get("name") == "web_fetch":
                self._add(next(iter(urls)), "page", text)
            elif call.get("name") == "web_search":
                data = json.loads(text)
                rows = data.get("results", []) if isinstance(data, dict) else data
                for row in rows[:200]:
                    if not isinstance(row, dict):
                        continue
                    url = normalize_url(row.get("url"))
                    snippet = row.get("snippet", row.get("content"))
                    if url in urls and isinstance(snippet, str):
                        self._add(url, "search_snippet", snippet)

    def _add(self, url, kind, text):
        # Each observation stays separate: quotes cannot span two fetches.
        if not text.strip() or len(self.sources) >= 200 and url not in self.sources:
            return
        text = text[: min(100_000, 2_000_000 - self._chars)]
        if not text:
            return
        docs = self.sources.setdefault(url, [])
        if len(docs) < 4 and (kind, text) not in docs:
            docs.append((kind, text))
            self._chars += len(text)

    def observe_resume(self, call, content, status):
        """A successful read must match a snapshotted, authorized upload."""
        if status != "success" or not isinstance(content, str) or content.startswith(("Error:", "(empty)")):
            return
        path = call.get("args", {}).get("path")
        with self._lock:
            for filename, source in self.resumes.items():
                if path == "/mnt/user-data/uploads/" + filename:
                    # Preserve only an actually observed span, never grant the
                    # rest of a file merely because one ranged read succeeded.
                    span = ground_excerpt(source, content)
                    if span:
                        self.read_resumes[filename] = span

    def validate(self, candidates):
        with self._lock:
            if not isinstance(candidates, list) or len(candidates) > MAX_ITEMS:
                self.rejected["invalid_payload"] += 1
                return []
            accepted = []
            for item in candidates:
                valid, reason = self._validate_one(item)
                if reason:
                    self.rejected[reason] += 1
                elif valid not in accepted:
                    accepted.append(valid)
                    if valid not in self.items and len(self.items) < MAX_ITEMS:
                        self.items.append(valid)
            return accepted

    def _validate_one(self, item):
        if not isinstance(item, dict) or not isinstance(item.get("section"), str) or item["section"] not in SECTIONS or not _string(item.get("claim"), 2000) or not _string(item.get("quote"), 4000):
            return None, "invalid_fields"
        url = normalize_url(item.get("url"))
        docs = self.sources.get(url, [])
        if not docs:
            return None, "unseen_url"
        matches = [(kind, text, ground_excerpt(text, item["quote"])) for kind, text in sorted(docs, key=lambda doc: doc[0] != "page")]
        matches = [(kind, text, quote) for kind, text, quote in matches if quote]
        if not matches:
            return None, "quote_not_found"
        published = None
        if item["section"] == "news":
            value = item.get("published_at")
            try:
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError()
                published = date.fromisoformat(value)
            except (ValueError, TypeError):
                return None, "missing_publication_date"
            if not _previous_year(self.as_of) <= published <= self.as_of:
                return None, "outside_recent_window"
            date_quote = item.get("published_at_quote", item["quote"])
            if not _string(date_quote, 4000):
                return None, "publication_date_not_grounded"
            matches = [(kind, text, quote) for kind, text, quote in matches if ground_excerpt(text, date_quote) and _date_in_quote(published, date_quote)]
            if not matches:
                return None, "publication_date_not_grounded"
        kind, text, quote = matches[0]
        result = {"section": item["section"], "claim": item["claim"].strip(), "url": url, "quote": quote, "source_kind": kind}
        if published:
            result.update(published_at=published.isoformat(), published_at_quote=ground_excerpt(text, item.get("published_at_quote", item["quote"])))
        if item["section"] == "gap":
            filename, resume_quote = item.get("resume_file"), item.get("resume_quote")
            if not isinstance(filename, str) or not _string(resume_quote, 4000) or not (span := ground_excerpt(self.read_resumes.get(filename, ""), resume_quote)):
                return None, "resume_quote_not_found"
            result.update(resume_file=filename, resume_quote=span)
        return result, None
