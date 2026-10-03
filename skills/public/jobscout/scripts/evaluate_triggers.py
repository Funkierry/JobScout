"""Run the production rule classifier offline; never instantiate a model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from app.jobscout.intent import classify_rules  # noqa: E402 - standalone script adds backend first


def evaluate(cases):
    rows = []
    for index, case in enumerate(cases, 1):
        decision = classify_rules(
            case["query"],
            anchor=case.get("anchor"),
            has_resume=case.get("has_resume", False),
            has_base=case.get("has_base", False),
            mode_hint=case.get("mode_hint"),
        )
        actual = bool(decision and decision.in_scope)
        rows.append(
            {
                "index": index,
                "query": case["query"],
                "expected": case["should_trigger"],
                "actual": actual,
                "unresolved": decision is None,
                "decision": decision.public() if decision else None,
            }
        )
    positives = sum(row["expected"] for row in rows)
    negatives = len(rows) - positives
    leaked = sum(row["actual"] and not row["expected"] for row in rows)
    rejected = sum(row["expected"] and not row["actual"] for row in rows)
    return {
        "method": "production_rules_only; unresolved returns fixed clarification; no model",
        "total": len(rows),
        "positive_count": positives,
        "negative_count": negatives,
        "off_topic_leak_count": leaked,
        "off_topic_leak_rate": leaked / negatives if negatives else None,
        "false_rejection_count": rejected,
        "false_rejection_rate": rejected / positives if positives else None,
        "unresolved_count": sum(row["unresolved"] for row in rows),
        "misclassified": [row for row in rows if row["actual"] != row["expected"]],
        "unresolved": [row for row in rows if row["unresolved"]],
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "skills/public/jobscout/evals/trigger_eval_set.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(json.loads(args.cases.read_text(encoding="utf-8")))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Only aggregate numbers go to stdout; fixtures or user prompts may be private.
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"rows", "misclassified", "unresolved"}},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
