#!/usr/bin/env python3
"""Check one application URL with the persistent Playwright login flow."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.application_tracker.browser import (  # noqa: E402
    BrowserAccessConfig,
    BrowserEvent,
    PersistentBrowserService,
)
from app.application_tracker.models import CheckResult  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Application status page URL")
    parser.add_argument(
        "--user-id",
        default=os.getenv("APPLICATION_TRACKER_USER_ID", "local-user"),
        help="Local profile namespace; the value is hashed before use in a path",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        help="Override APPLICATION_TRACKER_BROWSER_PROFILE_DIR",
    )
    parser.add_argument(
        "--snapshot-output",
        type=Path,
        help="Explicitly save extracted page text locally; may contain personal data",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    config = BrowserAccessConfig.from_env()
    if args.profile_dir:
        config = replace(config, profile_root=args.profile_dir.resolve())

    def report_event(event: BrowserEvent) -> None:
        print(json.dumps({"event": event.type.value, "message": event.message}))

    service = PersistentBrowserService()
    try:
        result = await service.fetch(
            args.url,
            user_id=args.user_id,
            config=config,
            on_event=report_event,
        )
    finally:
        await service.aclose()

    if args.snapshot_output and result.page_text:
        output = args.snapshot_output.resolve()
        await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(output.write_text, result.page_text, encoding="utf-8")

    print(
        json.dumps(
            {
                "check_result": result.check_result.value,
                "login_state": result.login_state.value,
                "login_attempted": result.login_attempted,
                "page_text_chars": len(result.page_text),
                "error_code": result.error_code,
            }
        )
    )
    if result.check_result is CheckResult.SUCCESS:
        return 0
    if result.check_result is CheckResult.LOGIN_REQUIRED:
        return 2
    return 1


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
