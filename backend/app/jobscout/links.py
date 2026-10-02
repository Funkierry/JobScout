"""Pure, offline URL provenance checks shared with the evaluation CLI.

An observed URL proves only that a tool returned that source, not that a claim
is true. Callers must supply actual, current-run tool events, never model-written
summaries or user messages masquerading as tool results.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

UNVERIFIED = "（来源未核验）"
MAX_TOOL_CHARS = 1_000_000
MAX_URL_CHARS = 8192
MAX_REPORT_CHARS = 1_000_000
_BARE = re.compile(r"(?:https?://|www\.)[^\s<>\[\]\"'`]+", re.I)
_DEFINITION = re.compile(r"^ {0,3}\[([^\]\n]+)\]:\s*(<[^>\n]+>|\S+)(?:[^\n]*)$", re.M)
_TAG = re.compile(r"<[^>\n]*>")
_ANCHOR = re.compile(r"<a\b[^>]*>(.*?)</a\s*>", re.I | re.S)
_ATTR = re.compile(r"\b(?:href|src|action|data)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.I)


def normalize_url(value: Any) -> str | None:
    """Canonicalize conservatively; never collapse paths, queries, or schemes."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_CHARS:
        return None
    value = html.unescape(value).strip()
    if re.search(r"[\s\\<>\"`\x00-\x1f\x7f]", value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
            return None
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        port = parts.port
        if port and (parts.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
            host += f":{port}"
        return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError):
        return None


def _get(message: Any, key: str, default: Any = None) -> Any:
    return message.get(key, default) if isinstance(message, dict) else getattr(message, key, default)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def collect_seen_urls(tool_messages: Iterable[Any]) -> set[str]:
    """Collect search result URL fields and completed fetches paired by call ID.

    For Markdown-only fetch providers the successful tool result confirms its
    requested URL. Arbitrary URLs in fetched prose/search snippets are not added.
    Error, empty, unpaired, and non-web results contribute nothing.
    """
    pending: dict[str, dict] = {}
    seen: set[str] = set()
    for message in tool_messages:
        kind = _get(message, "type", _get(message, "role"))
        if kind in {"ai", "assistant"}:
            for call in _get(message, "tool_calls", []) or []:
                if isinstance(call, dict) and call.get("id"):
                    pending[call["id"]] = call
            continue
        if kind != "tool":
            continue
        call = pending.pop(_get(message, "tool_call_id"), {})
        name = _get(message, "name") or call.get("name")
        if name not in {"web_search", "web_fetch"} or _get(message, "status") in {"error", "failed", "cancelled"}:
            continue
        content = _text(_get(message, "content", "")).strip()
        if not content or len(content) > MAX_TOOL_CHARS or re.match(r"(?:error\b|failed\b|no (?:results|content)\b)", content, re.I):
            continue
        if name == "web_fetch":
            if content.startswith("{"):
                try:
                    payload = json.loads(content)
                except ValueError:
                    payload = {}
                if isinstance(payload, dict) and (payload.get("error") or payload.get("status") in {"error", "failed"}):
                    continue
            if call.get("name") == name:
                url = normalize_url(call.get("args", {}).get("url"))
                if url:
                    seen.add(url)
            continue
        try:
            result = json.loads(content)
        except (ValueError, TypeError):
            continue
        if isinstance(result, dict):
            if result.get("error") or result.get("status") in {"error", "failed"}:
                continue
            result = result.get("results", [])
        if isinstance(result, list):
            for row in result:
                url = normalize_url(row.get("url")) if isinstance(row, dict) else None
                if url:
                    seen.add(url)
    return seen


def _closing_pairs(text: str) -> dict[int, int]:
    """Index balanced delimiters once, including malformed bracket-heavy input."""
    stacks: dict[str, list[int]] = {"[": [], "(": []}
    pairs = {}
    escaped = False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in stacks:
            stacks[char].append(i)
        elif char in "])":
            stack = stacks["[" if char == "]" else "("]
            if stack:
                pairs[stack.pop()] = i
    return pairs


def _destination(value: str) -> str:
    value = value.strip()
    if value.startswith("<"):
        value = value[1:].split(">", 1)[0]
    else:
        value = value.split(maxsplit=1)[0] if value else ""
    return re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]^_`{|}~])", r"\1", value)


def strip_unseen_links(report_md: str, seen: Iterable[str]) -> tuple[str, int]:
    """Rewrite unobserved destinations, preserving ordinary Markdown structure.

    Reference links become inline links. HTML anchors become Markdown; other
    HTML tags are removed to keep raw HTML/resource attributes out of downloads.
    Bare URLs (including inside code) are checked too. Relative/non-HTTP links
    are unverified. Counts represent rejected link occurrences; an unused bad
    reference definition counts once, a used definition counts at each use.
    """
    if len(report_md) > MAX_REPORT_CHARS:
        raise ValueError("JobScout report exceeds the link validation limit")
    allowed = {url for item in seen if (url := normalize_url(item))}
    removed = 0
    references: dict[str, str] = {}
    used_refs: set[str] = set()

    def refkey(label: str) -> str:
        return " ".join(label.lower().split())

    def definition(match: re.Match) -> str:
        references.setdefault(refkey(match[1]), _destination(match[2]))
        return ""

    source = _DEFINITION.sub(definition, report_md)

    def link(label: str, destination: str) -> str:
        nonlocal removed
        url = normalize_url(destination)
        if url and url in allowed:
            # Angle-delimited destinations remain valid with balanced parentheses.
            target = f"<{url}>" if any(c in url for c in "()") else url
            return f"[{label}]({target})"
        removed += 1
        return label + UNVERIFIED

    def plain(text: str) -> str:
        nonlocal removed

        def bare(match: re.Match) -> str:
            nonlocal removed
            raw = match[0]
            suffix = ""
            while raw and raw[-1] in ".,;:!?，。；：！？）":
                suffix = raw[-1] + suffix
                raw = raw[:-1]
            # A closing parenthesis surrounding prose is not part of the URL.
            while raw.endswith(")") and raw.count(")") > raw.count("("):
                suffix = ")" + suffix
                raw = raw[:-1]
            url = normalize_url(raw)
            if url and url in allowed:
                return url + suffix
            removed += 1
            return UNVERIFIED + suffix

        return _BARE.sub(bare, text)

    def scan(text: str, depth: int = 0) -> str:
        nonlocal removed
        if depth > 16:
            removed += 1
            return UNVERIFIED
        result: list[str] = []
        pairs = _closing_pairs(text)
        cursor = 0
        plain_start = 0
        while cursor < len(text):
            end = None
            replacement = None
            if text[cursor] == "[" or text.startswith("![", cursor):
                label_start = cursor + (1 if text[cursor] == "!" else 0)
                label_end = pairs.get(label_start)
                if label_end is not None:
                    label = text[label_start + 1 : label_end]
                    after = label_end + 1
                    dest = None
                    if after < len(text) and text[after] == "(":
                        close = pairs.get(after)
                        if close is not None:
                            end, dest = close + 1, _destination(text[after + 1 : close])
                    elif after < len(text) and text[after] == "[":
                        close = text.find("]", after + 1)
                        if close >= 0:
                            key = refkey(text[after + 1 : close] or label)
                            if key in references:
                                end, dest = close + 1, references[key]
                                used_refs.add(key)
                    elif refkey(label) in references:
                        key = refkey(label)
                        end, dest = after, references[key]
                        used_refs.add(key)
                    if dest is not None:
                        replacement = link(scan(label, depth + 1), dest)
            elif text[cursor] == "<":
                anchor = _ANCHOR.match(text, cursor)
                tag = _TAG.match(text, cursor)
                if anchor:
                    attr = _ATTR.search(anchor[0].split(">", 1)[0])
                    label = scan(anchor[1], depth + 1)
                    replacement = link(label, next((v for v in attr.groups() if v is not None), "")) if attr else label
                    end = anchor.end()
                elif tag:
                    raw = tag[0][1:-1]
                    attr = _ATTR.search(tag[0])
                    if re.match(r"(?:[a-z][a-z0-9+.-]*:|www\.)", raw, re.I):
                        url = normalize_url(raw)
                        if url and url in allowed:
                            replacement = f"<{url}>"
                        else:
                            removed += 1
                            replacement = UNVERIFIED
                    elif attr:
                        replacement = link("", next((v for v in attr.groups() if v is not None), ""))
                    else:
                        replacement = ""
                    end = tag.end()
            if replacement is not None and end is not None:
                result.extend((plain(text[plain_start:cursor]), replacement))
                cursor = plain_start = end
            else:
                cursor += 1
        result.append(plain(text[plain_start:]))
        return "".join(result)

    cleaned = scan(source)
    for key, dest in references.items():
        if key not in used_refs and normalize_url(dest) not in allowed:
            removed += 1
            cleaned += "\n" + UNVERIFIED
    return cleaned, removed
