#!/usr/bin/env python3
"""Run the Phase-3 LangGraph agent for one application status URL."""

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

from app.application_tracker.agent import ApplicationTrackerAgent  # noqa: E402
from app.application_tracker.browser import BrowserAccessConfig, BrowserEvent  # noqa: E402
from app.application_tracker.models import ApplicationInput, CheckResult  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Application status page URL")
    parser.add_argument("--company", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--applied-at", default=None, help="YYYY-MM-DD")
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--user-id",
        default=os.getenv("APPLICATION_TRACKER_USER_ID", "local-user"),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="DeerFlow model profile; defaults to APPLICATION_TRACKER_AGENT_MODEL",
    )
    parser.add_argument("--profile-dir", type=Path)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    application = ApplicationInput(
        company=args.company,
        role=args.role,
        url=args.url,
        applied_at=args.applied_at,
        notes=args.notes,
    )
    browser_config = BrowserAccessConfig.from_env()
    if args.profile_dir:
        browser_config = replace(
            browser_config,
            profile_root=args.profile_dir.resolve(),
        )

    def report_event(event: BrowserEvent) -> None:
        print(json.dumps({"event": event.type.value, "message": event.message}))

    agent = ApplicationTrackerAgent.from_model_name(args.model)
    try:
        record = await agent.run(
            application,
            user_id=args.user_id,
            browser_config=browser_config,
            on_event=report_event,
        )
    finally:
        await agent.aclose()

    print(json.dumps(record.model_dump(mode="json")))
    if record.check_result is CheckResult.SUCCESS:
        return 0
    if record.check_result is CheckResult.LOGIN_REQUIRED:
        return 2
    return 1


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
