#!/usr/bin/env python3
"""Evaluate normalized status extraction against synthetic page snapshots."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.application_tracker.evaluation import evaluate_cases  # noqa: E402
from app.application_tracker.extractor import DEFAULT_MAX_PAGE_CHARS, StatusExtractor  # noqa: E402
from app.application_tracker.io import load_evaluation_cases, write_evaluation_report, write_status_csv  # noqa: E402
from deerflow.models import create_chat_model  # noqa: E402

DEFAULT_DATASET = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "application_tracker" / "eval_cases.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, required=True, help="Destination JSON evaluation report")
    parser.add_argument("--predictions", type=Path, help="Optional normalized prediction CSV")
    parser.add_argument("--model", default=None, help="DeerFlow model profile; defaults to APPLICATION_TRACKER_MODEL or the first configured model")
    parser.add_argument("--max-page-chars", type=int, default=DEFAULT_MAX_PAGE_CHARS)
    parser.add_argument("--min-accuracy", type=float, default=None, help="Exit non-zero when status accuracy is below this threshold")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.min_accuracy is not None and not 0 <= args.min_accuracy <= 1:
        raise SystemExit("--min-accuracy 必须在 0 到 1 之间")

    model_name = args.model or os.getenv("APPLICATION_TRACKER_MODEL") or None
    model = create_chat_model(name=model_name, thinking_enabled=False)
    extractor = StatusExtractor.from_chat_model(model, max_page_chars=args.max_page_chars)
    report = evaluate_cases(load_evaluation_cases(args.dataset), extractor)
    write_evaluation_report(args.report, report)
    if args.predictions:
        write_status_csv(args.predictions, [result.record for result in report.results if result.record is not None])

    print(f"Cases: {report.total}")
    print(f"Status accuracy: {report.status_accuracy:.1%}")
    print(f"Schema validity: {report.schema_valid_rate:.1%}")
    print(f"Evidence grounding: {report.evidence_grounded_rate:.1%}")
    print(f"Check-result accuracy: {report.check_result_accuracy:.1%}")
    print(f"Report: {args.report}")
    if args.min_accuracy is not None and report.status_accuracy < args.min_accuracy:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
