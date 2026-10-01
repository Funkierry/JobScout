#!/usr/bin/env python3
"""Explicitly opt in to model-backed offline baseline evaluation."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent
PRIVATE_ROOT = (ROOT / "local_eval").resolve()
sys.path.insert(0, str(BACKEND_DIR))

from app.application_tracker.evaluation import evaluate_cases  # noqa: E402
from app.application_tracker.extractor import StatusExtractor  # noqa: E402
from app.application_tracker.io import load_evaluation_cases, write_evaluation_report  # noqa: E402
from deerflow.models import create_chat_model  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PRIVATE_ROOT / "tracker_snapshots" / "labels.jsonl")
    parser.add_argument("--report", type=Path, default=PRIVATE_ROOT / "tracker_snapshots" / "baseline.json")
    parser.add_argument("--model", default=None)
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    parser.add_argument("--allow-model-cost", action="store_true", help="Required because each case invokes the configured model")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.allow_model_cost:
        raise SystemExit("This command calls a model; pass --allow-model-cost explicitly.")
    if not args.dataset.resolve().is_relative_to(PRIVATE_ROOT) or not args.report.resolve().is_relative_to(PRIVATE_ROOT):
        raise SystemExit("Real baseline inputs and outputs must remain under local_eval/.")
    cases = load_evaluation_cases(args.dataset)
    if not cases:
        raise SystemExit("No labeled cases found.")
    model_name = args.model or os.getenv("APPLICATION_TRACKER_MODEL") or None
    model = create_chat_model(name=model_name, thinking_enabled=False)
    report = evaluate_cases(
        cases,
        StatusExtractor.from_chat_model(model),
        input_usd_per_million=args.input_usd_per_million,
        output_usd_per_million=args.output_usd_per_million,
    )
    write_evaluation_report(args.report, report)
    print(f"Cases: {report.total}")
    print(f"Status accuracy: {report.status_accuracy:.1%}")
    print(f"Unknown rate: {report.unknown_rate:.1%}")
    print(f"Verbatim evidence rate: {report.verbatim_evidence_rate if report.verbatim_evidence_rate is not None else 'N/A'}")
    print(f"Mean time per case: {report.mean_elapsed_ms:.1f} ms")
    print(f"Token coverage: {report.token_coverage:.1%}")
    print(f"Cost (USD): {report.total_cost_usd if report.total_cost_usd is not None else 'N/A'}")
    print(f"Private report: {args.report}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Baseline failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
