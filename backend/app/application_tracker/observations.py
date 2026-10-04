"""Bounded, untrusted observations shared by capture, runtime and offline replay."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.application_tracker.redaction import redact_json, sanitize_url


class SourceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["json", "dom"]
    text: str = Field(max_length=50_000)
    url: str = Field(default="", max_length=2048)


async def read_json_response(response: Any, *, max_json_bytes: int = 256_000) -> dict[str, Any]:
    entry = {"url": sanitize_url(response.url), "status": response.status, "body": None, "truncated": False}
    try:
        length = response.headers.get("content-length")
        if length and int(length) > max_json_bytes:
            entry["truncated"] = True
        else:
            # Playwright buffers a response before body(); the limit bounds retained
            # data, not Chromium's network buffer (especially without Content-Length).
            body = await asyncio.wait_for(response.body(), timeout=5)
            if len(body) > max_json_bytes:
                entry["truncated"] = True
            else:
                entry["body"] = redact_json(json.loads(body))
    except (ValueError, UnicodeDecodeError, TimeoutError, OSError, RecursionError):
        entry["capture_error"] = "unreadable_json"
    except Exception:
        entry["capture_error"] = "response_unavailable"
    return entry


def same_host(url: str, page_url: str) -> bool:
    try:
        parts, page = urlsplit(url), urlsplit(page_url)
        return parts.scheme in {"http", "https"} and bool(parts.hostname) and parts.hostname.lower().rstrip(".") == (page.hostname or "").lower().rstrip(".")
    except ValueError:
        return False


class JsonCollector:
    """One navigation's response window; callbacks never log private payloads."""

    def __init__(self, page_url: str, *, max_responses: int = 20, max_json_bytes: int = 256_000):
        self.page_url = page_url
        self.max_responses = max_responses
        self.max_json_bytes = max_json_bytes
        self.responses: list[dict[str, Any]] = []
        self._tasks: list[asyncio.Task] = []
        self._closed = False

    def on_response(self, response: Any) -> None:
        if self._closed or len(self._tasks) >= self.max_responses:
            return
        if not same_host(response.url, self.page_url) or not 200 <= response.status < 300:
            return
        if response.request.resource_type not in {"xhr", "fetch"} or "json" not in response.headers.get("content-type", "").lower():
            return
        # Reserve slots in arrival order, independently of body completion order.
        index = len(self.responses)
        self.responses.append({})

        async def capture():
            self.responses[index] = await read_json_response(response, max_json_bytes=self.max_json_bytes)

        self._tasks.append(asyncio.create_task(capture()))

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks))

    async def close(self) -> None:
        self._closed = True
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


_NOISE_KEYS = re.compile(r"password|secret|token|auth|cookie|session|email|phone|mobile|avatar|image|html|description|recommend|navigation|telemetry|analytics", re.I)
_ROLE_KEYS = re.compile(r"role|job|position|岗位|职位", re.I)
_STATUS_KEYS = re.compile(r"status|stage|progress|state|状态|环节|进度", re.I)


def _prune(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(value, dict):
        return {str(key)[:100]: _prune(item, depth + 1) for key, item in list(value.items())[:80] if not _NOISE_KEYS.search(str(key))}
    if isinstance(value, list):
        return [_prune(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return value[:2000]
    return value


def _records(value: Any):
    if isinstance(value, dict):
        # Arrays are independent records. Never ground row A against row B.
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _records(child)
        scalar_tree = {key: item for key, item in value.items() if not isinstance(item, list)}
        keys = " ".join(str(key) for key in scalar_tree)
        if _ROLE_KEYS.search(keys) and _STATUS_KEYS.search(keys):
            yield scalar_tree
    elif isinstance(value, list):
        for child in value:
            yield from _records(child)


def json_observations(responses: list[dict[str, Any]], *, page_url: str, max_chars: int = 30_000) -> list[SourceObservation]:
    """Project generic application-shaped objects, without ATS/status adapters."""
    sources: list[SourceObservation] = []
    remaining = max_chars
    seen: set[str] = set()
    for response in responses[:20]:
        if not same_host(str(response.get("url", "")), page_url) or not 200 <= response.get("status", 0) < 300 or response.get("truncated"):
            continue
        for record in _records(redact_json(_prune(response.get("body")))):
            text = json.dumps(record, ensure_ascii=False, indent=2)
            if text in seen or len(text) > remaining:
                continue
            seen.add(text)
            remaining -= len(text)
            sources.append(SourceObservation(kind="json", text=text, url=sanitize_url(response["url"])[:2048]))
            if len(sources) >= 100:
                return sources
    return sources


def dom_observation(text: str, *, max_chars: int = 50_000) -> SourceObservation:
    return SourceObservation(kind="dom", text=text[: min(max_chars, 50_000)])
