#!/usr/bin/env python3
"""Replay captured ATS JSON offline, without initializing or calling any model."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent
PRIVATE_ROOT = (ROOT / "local_eval").resolve()
sys.path.insert(0, str(BACKEND_DIR))

from app.application_tracker.annotation import attach_snapshot_observations  # noqa: E402
from app.application_tracker.evaluation import evaluate_cases  # noqa: E402
from app.application_tracker.io import load_evaluation_cases, write_evaluation_report  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PRIVATE_ROOT / "tracker_snapshots/labels.jsonl")
    parser.add_argument("--snapshots", type=Path, default=PRIVATE_ROOT / "tracker_snapshots")
    parser.add_argument("--report", type=Path, default=PRIVATE_ROOT / "tracker_snapshots/stage5-adapters.json")
    args = parser.parse_args(argv)
    if any(not path.resolve().is_relative_to(PRIVATE_ROOT) for path in (args.dataset, args.snapshots, args.report)):
        raise SystemExit("Real replay inputs and outputs must remain under local_eval/.")
    cases = attach_snapshot_observations(load_evaluation_cases(args.dataset), args.snapshots)
    if not cases:
        raise SystemExit("No labeled cases found.")
    report = evaluate_cases(cases, adapters_only=True)
    write_evaluation_report(args.report, report)
    metadata = {
        "algorithm": "jobscout-stage5-adapters-v1",
        "mode": "adapters-only-no-llm-fallback",
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "responses_sha256": hashlib.sha256(json.dumps([[item.model_dump() for item in case.json_responses] for case in cases], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()),
        "browser_timing": "not_measured_offline",
        "confidence": "rule_score_not_probability",
    }
    args.report.with_suffix(".manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Deliberately print aggregates only, never records, roles, URLs or excerpts.
    print(json.dumps(report.model_dump(mode="json", exclude={"results"}), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Adapter replay failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
