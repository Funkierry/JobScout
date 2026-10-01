"""Read-only, local-only capture of application-page baseline evidence."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from app.application_tracker.browser.detector import LoginDetector
from app.application_tracker.browser.models import BrowserAccessConfig, LoginState
from app.application_tracker.browser.network import guard_route, validate_navigation_url
from app.application_tracker.browser.playwright_adapter import PlaywrightPersistentContextLauncher
from app.application_tracker.browser.profiles import profile_directory

DEFAULT_MAX_JSON_BYTES = 256_000
DEFAULT_MAX_JSON_RESPONSES = 100
_SENSITIVE_KEY = re.compile(
    r"(?:password|secret|token|auth|cookie|csrf|session|email|phone|mobile|telephone|"
    r"address|passport|idcard|national.?id|candidate.?id|applicant.?id|user.?id|user.?name|api.?key|private.?key|"
    r"record.?id|full.?name|first.?name|last.?name|birth|^id$)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_LONG_NUMBER = re.compile(r"(?<!\d)\d{11,19}(?!\d)")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+")
_PRIVATE_SEGMENT = re.compile(r"(?:\d+|[a-fA-F0-9]{16,}|[a-fA-F0-9-]{32,}|(?=[A-Za-z0-9_-]{20,}$)(?=.*\d)[A-Za-z0-9_-]+)")


class SnapshotLoginRequired(RuntimeError):
    """The saved profile needs an interactive login before capture."""


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
        value = _EMAIL.sub("[REDACTED]", value)
        value = _PHONE.sub("[REDACTED]", value)
        value = _LONG_NUMBER.sub("[REDACTED]", value)
        return _BEARER.sub("[REDACTED]", value)
    return value


def save_snapshot(root: Path, snapshot_id: str, *, body_text: str, responses: list[dict[str, Any]], screenshot: bytes, metadata: dict[str, Any]) -> Path:
    destination = root / snapshot_id
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "body.txt").write_text(body_text, encoding="utf-8")
    (destination / "responses.json").write_text(json.dumps(responses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "screenshot.png").write_bytes(screenshot)
    return destination


async def capture_snapshot(
    url: str,
    *,
    user_id: str,
    output_root: Path,
    config: BrowserAccessConfig | None = None,
    launcher: Any | None = None,
    detector: Any | None = None,
    max_json_bytes: int = DEFAULT_MAX_JSON_BYTES,
    max_json_responses: int = DEFAULT_MAX_JSON_RESPONSES,
) -> Path:
    """Capture one page without invoking the tracker model or writing its database."""
    if max_json_bytes <= 0 or max_json_responses <= 0:
        raise ValueError("JSON capture limits must be positive")
    settings = config or BrowserAccessConfig.from_env()
    error = await validate_navigation_url(url, allow_private_addresses=settings.allow_private_addresses)
    if error:
        raise ValueError("Application URL failed the tracker network guard")

    profile_dir = profile_directory(settings.profile_root, user_id=user_id, url=url)
    await asyncio.to_thread(profile_dir.mkdir, parents=True, exist_ok=True)
    own_launcher = launcher is None
    launcher = launcher or PlaywrightPersistentContextLauncher()
    detector = detector or LoginDetector()
    context = None
    response_tasks: list[asyncio.Task[None]] = []
    responses: list[dict[str, Any]] = []
    omitted_responses = 0
    capture_open = True

    async def capture_response(response: Any) -> None:
        entry: dict[str, Any] = {
            "url": sanitize_url(response.url),
            "status": response.status,
            "body": None,
            "truncated": False,
        }
        try:
            length = response.headers.get("content-length")
            if length and int(length) > max_json_bytes:
                entry["truncated"] = True
            else:
                body = await asyncio.wait_for(response.body(), timeout=5)
                if len(body) > max_json_bytes:
                    entry["truncated"] = True
                else:
                    entry["body"] = redact_json(json.loads(body))
        except (ValueError, UnicodeDecodeError, TimeoutError, OSError):
            entry["capture_error"] = "unreadable_json"
        except Exception:
            entry["capture_error"] = "response_unavailable"
        responses.append(entry)

    def on_response(response: Any) -> None:
        nonlocal omitted_responses
        if not capture_open:
            return
        request = response.request
        if request.resource_type not in {"xhr", "fetch"}:
            return
        if "json" not in response.headers.get("content-type", "").lower():
            return
        if len(response_tasks) >= max_json_responses:
            omitted_responses += 1
            return
        response_tasks.append(asyncio.create_task(capture_response(response)))

    try:
        context = await launcher.launch(profile_dir, headless=settings.headless, timeout_ms=settings.navigation_timeout_ms)

        async def route_handler(route: Any) -> None:
            await guard_route(route, allow_private_addresses=settings.allow_private_addresses)

        await context.route("**/*", route_handler)
        context.on("response", on_response)
        page = context.pages[-1] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=settings.navigation_timeout_ms)
        try:
            await page.wait_for_load_state("networkidle", timeout=min(settings.navigation_timeout_ms, 3_000))
        except Exception:
            pass
        await page.wait_for_timeout(min(settings.page_text_timeout_ms, 2_000))
        capture_open = False
        inspection = await detector.inspect(page)
        if inspection.state is not LoginState.AUTHENTICATED:
            raise SnapshotLoginRequired("Profile is not authenticated; use the existing browser login check first")
        body_text = await page.locator("body").inner_text(timeout=settings.page_text_timeout_ms)
        screenshot = await page.screenshot(type="png", full_page=False)
        final_url = sanitize_url(page.url)
        if response_tasks:
            await asyncio.gather(*response_tasks)
        snapshot_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        metadata = {
            "snapshot_id": snapshot_id,
            "captured_at": datetime.now(UTC).isoformat(),
            "source_url": sanitize_url(url),
            "final_url": final_url,
            "body_chars": len(body_text),
            "json_response_count": len(responses),
            "omitted_json_responses": omitted_responses,
            "max_json_bytes": max_json_bytes,
            "capture_window": "domcontentloaded + networkidle up to 3s + 2s settle",
        }
        return await asyncio.to_thread(
            save_snapshot,
            output_root,
            snapshot_id,
            body_text=body_text,
            responses=responses,
            screenshot=screenshot,
            metadata=metadata,
        )
    finally:
        for task in response_tasks:
            if not task.done():
                task.cancel()
        if response_tasks:
            await asyncio.gather(*response_tasks, return_exceptions=True)
        if context is not None:
            await context.close()
        if own_launcher:
            await launcher.aclose()
