"""Network guard applied to initial URLs, redirects, and subresources."""

from __future__ import annotations

import asyncio
from typing import Protocol
from urllib.parse import urlsplit

from deerflow.community.url_safety import validate_public_http_url


class RequestLike(Protocol):
    url: str


class RouteLike(Protocol):
    request: RequestLike

    async def abort(self, error_code: str = "failed") -> None: ...

    async def continue_(self) -> None: ...


async def validate_navigation_url(url: str, *, allow_private_addresses: bool) -> str | None:
    parsed = urlsplit(url)
    if parsed.username or parsed.password:
        return "Error: URL must not contain embedded credentials"
    return await asyncio.to_thread(
        validate_public_http_url,
        url,
        allow_private_addresses=allow_private_addresses,
        action="open an application status page at",
    )


async def guard_route(route: RouteLike, *, allow_private_addresses: bool = False) -> None:
    url = route.request.url
    if urlsplit(url).scheme not in {"http", "https"}:
        await route.continue_()
        return
    error = await validate_navigation_url(url, allow_private_addresses=allow_private_addresses)
    if error:
        await route.abort("blockedbyclient")
        return
    await route.continue_()
