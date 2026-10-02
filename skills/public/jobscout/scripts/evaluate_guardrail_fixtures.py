"""Compare phase-1 link filtering with phase-2 validation on synthetic data.

This measures guard behavior only. No model call, true quality estimate, or
inferred token/latency/cost is involved.
"""

import argparse
import json
from pathlib import Path

from grade_runs import replay_structured_report
from app.jobscout.links import collect_seen_urls, strip_unseen_links
from app.jobscout.render import cell


def evaluate(path):
    fixtures = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for fixture in fixtures:
        run = fixture["run"]
        before = strip_unseen_links(fixture["phase1_draft"], collect_seen_urls(run.get("tool_messages", [])))[0]
        after, stats = replay_structured_report(run, fixture["mode"])
        row = {"id": fixture["id"], "mode": fixture["mode"],
               "before_contains_target": fixture["target"] in before,
               "after_contains_target": cell(fixture["target"]) in after, **stats}
        row["matches_expectation"] = row["after_contains_target"] == fixture["expected_after"]
        rows.append(row)
    return {"fixture_count": len(rows), "expectations_met": sum(row["matches_expectation"] for row in rows), "results": rows,
            "real_model_quality": "待运行", "real_model_latency": "待运行", "real_model_tokens": "待运行", "real_model_cost": "待运行"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(__file__).resolve().parents[1] / "evals/fixtures/structured_guardrails.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.input)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["expectations_met"] == result["fixture_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
