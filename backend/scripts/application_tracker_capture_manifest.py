#!/usr/bin/env python3
"""Capture private tracker URLs from a local manifest without model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PRIVATE_DIR = BACKEND_DIR.parent / "local_eval" / "tracker_snapshots"
MANIFEST_PATH = PRIVATE_DIR / "url_manifest.json"
PROGRESS_PATH = PRIVATE_DIR / "capture_progress.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Show counts without visiting URLs")
    parser.add_argument("--user-id", help="Tracker user ID used by the existing browser profile")
    parser.add_argument("--profile-dir", type=Path, help="Existing tracker browser profile root")
    parser.add_argument("--retry-all", action="store_true", help="Recapture completed URLs")
    args = parser.parse_args()

    manifest_bytes = MANIFEST_PATH.read_bytes()
    manifest = json.loads(manifest_bytes)
    entries = manifest["entries"]
    if args.list:
        print(f"Private manifest contains {len(entries)} URLs.")
        for category in sorted({entry["category"] for entry in entries}):
            print(f"{category}: {sum(entry['category'] == category for entry in entries)}")
        return 0

    user_id = args.user_id or os.getenv("APPLICATION_TRACKER_USER_ID")
    if not user_id:
        user_id = input("Tracker user ID (Enter for local-user): ").strip() or "local-user"
    profile_args = ["--profile-dir", str(args.profile_dir)] if args.profile_dir else []
    fingerprint = hashlib.sha256(manifest_bytes).hexdigest()
    completed: set[int] = set()
    if not args.retry_all and PROGRESS_PATH.exists():
        progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        if progress.get("manifest_sha256") == fingerprint:
            completed = set(progress.get("completed_ids", []))

    saved = skipped = failed = 0
    for entry in entries:
        entry_id = entry["id"]
        if entry_id in completed:
            skipped += 1
            continue
        print(f"[{entry_id}/{len(entries)}] Capturing application page", flush=True)
        base = [sys.executable, "scripts/application_tracker_snapshot.py", entry["url"], "--user-id", user_id, *profile_args]
        result = subprocess.run(base, cwd=BACKEND_DIR, check=False).returncode
        if result == 2:
            print("Login required. Complete sign-in in the browser window.", flush=True)
            login = [sys.executable, "scripts/application_tracker_browser_check.py", entry["url"], "--user-id", user_id, *profile_args]
            if subprocess.run(login, cwd=BACKEND_DIR, check=False).returncode == 0:
                result = subprocess.run(base, cwd=BACKEND_DIR, check=False).returncode
        if result == 0:
            saved += 1
            completed.add(entry_id)
            PROGRESS_PATH.write_text(
                json.dumps({"manifest_sha256": fingerprint, "completed_ids": sorted(completed)}, indent=2),
                encoding="utf-8",
            )
        else:
            failed += 1

    print(f"Done: {saved} saved, {skipped} already saved, {failed} incomplete.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
