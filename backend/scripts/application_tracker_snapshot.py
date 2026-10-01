#!/usr/bin/env python3
"""Capture one private tracker page locally; explicitly makes a browser request."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.application_tracker.browser.models import BrowserAccessConfig  # noqa: E402
from app.application_tracker.snapshot import SnapshotLoginRequired, capture_snapshot  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Your own application page URL; command-line history may retain it")
    parser.add_argument("--user-id", default=os.getenv("APPLICATION_TRACKER_USER_ID", "local-user"))
    parser.add_argument("--profile-dir", type=Path, help="Same profile root used by the existing tracker")
    parser.add_argument("--max-json-bytes", type=int, default=256_000)
    parser.add_argument("--max-json-responses", type=int, default=100)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    config = BrowserAccessConfig.from_env()
    if args.profile_dir:
        config = replace(config, profile_root=args.profile_dir.resolve())
    try:
        destination = await capture_snapshot(
            args.url,
            user_id=args.user_id,
            output_root=ROOT / "local_eval" / "tracker_snapshots",
            config=config,
            max_json_bytes=args.max_json_bytes,
            max_json_responses=args.max_json_responses,
        )
    except SnapshotLoginRequired:
        print("Existing browser profile needs login. Run application_tracker_browser_check.py first.")
        return 2
    except Exception as exc:
        print(f"Snapshot failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"Saved private snapshot: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
