#!/usr/bin/env python3
"""Extract normalized application statuses from saved page-text snapshots."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.application_tracker.extractor import DEFAULT_MAX_PAGE_CHARS, StatusExtractor  # noqa: E402
from app.application_tracker.io import load_offline_cases, write_status_csv  # noqa: E402
from app.application_tracker.models import ApplicationInput  # noqa: E402
from deerflow.models import create_chat_model  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 JSONL containing application metadata and page_text")
    parser.add_argument("--output", type=Path, required=True, help="Destination CSV using the normalized public contract")
    parser.add_argument("--model", default=None, help="DeerFlow model profile; defaults to APPLICATION_TRACKER_MODEL or the first configured model")
    parser.add_argument("--max-page-chars", type=int, default=DEFAULT_MAX_PAGE_CHARS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model_name = args.model or os.getenv("APPLICATION_TRACKER_MODEL") or None
    model = create_chat_model(name=model_name, thinking_enabled=False)
    extractor = StatusExtractor.from_chat_model(model, max_page_chars=args.max_page_chars)
    cases = load_offline_cases(args.input)
    records = [
        extractor.extract(
            ApplicationInput.model_validate(case.model_dump(include=set(ApplicationInput.model_fields))),
            case.page_text,
        )
        for case in cases
    ]
    write_status_csv(args.output, records)
    print(f"Processed {len(records)} records. Output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
