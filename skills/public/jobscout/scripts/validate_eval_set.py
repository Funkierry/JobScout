#!/usr/bin/env python3
"""Validate the JobScout eval set and estimate execution scale."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
BEHAVIOR_PATH = SKILL_ROOT / "evals" / "behavior_eval_set.json"
TRIGGER_PATH = SKILL_ROOT / "evals" / "trigger_eval_set.json"
RUN_SCHEMA_PATH = SKILL_ROOT / "evals" / "run_result.schema.json"
GRADER_FIXTURE_PATH = SKILL_ROOT / "evals" / "fixtures" / "grader_smoke.jsonl"

EXPECTED_CATEGORY_COUNTS = {
    "flow": 8,
    "evidence": 8,
    "resume_followup": 4,
    "base": 8,
    "environment_scope": 2,
}
VALID_TIERS = {"smoke", "regression"}
VALID_RISKS = {"critical", "high", "medium", "low"}
VALID_MODES = {"interview_prep", "feishu_base_matching", "boundary"}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_behavior(payload: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    require(payload.get("schema_version") == "1.0", "behavior schema_version must be 1.0", errors)
    profiles = payload.get("profiles")
    cases = payload.get("cases")
    require(isinstance(profiles, dict) and bool(profiles), "behavior profiles must be a non-empty object", errors)
    require(isinstance(cases, list), "behavior cases must be an array", errors)
    if not isinstance(profiles, dict) or not isinstance(cases, list):
        return {}

    ids: list[str] = []
    categories: Counter[str] = Counter()
    tiers: Counter[str] = Counter()
    risks: Counter[str] = Counter()
    modes: Counter[str] = Counter()

    for index, case in enumerate(cases):
        prefix = f"behavior case #{index + 1}"
        require(isinstance(case, dict), f"{prefix} must be an object", errors)
        if not isinstance(case, dict):
            continue
        case_id = case.get("id")
        require(isinstance(case_id, str) and bool(case_id.strip()), f"{prefix} needs a non-empty id", errors)
        if isinstance(case_id, str):
            ids.append(case_id)
        require(case.get("tier") in VALID_TIERS, f"{prefix} has invalid tier", errors)
        require(case.get("risk") in VALID_RISKS, f"{prefix} has invalid risk", errors)
        require(case.get("mode") in VALID_MODES, f"{prefix} has invalid mode", errors)
        require(isinstance(case.get("category"), str), f"{prefix} needs category", errors)
        require(isinstance(case.get("prompt"), str) and bool(case["prompt"].strip()), f"{prefix} needs prompt", errors)
        require(isinstance(case.get("setup"), str) and bool(case["setup"].strip()), f"{prefix} needs setup", errors)
        require(isinstance(case.get("tags"), list) and bool(case["tags"]), f"{prefix} needs tags", errors)

        expected = case.get("expected")
        require(isinstance(expected, dict), f"{prefix} needs expected object", errors)
        if isinstance(expected, dict):
            profile = expected.get("profile")
            require(profile in profiles, f"{prefix} references unknown profile {profile!r}", errors)
            require(isinstance(expected.get("action"), str), f"{prefix} needs expected.action", errors)

        categories[str(case.get("category"))] += 1
        tiers[str(case.get("tier"))] += 1
        risks[str(case.get("risk"))] += 1
        modes[str(case.get("mode"))] += 1

    require(len(ids) == len(set(ids)), "behavior case ids must be unique", errors)
    require(len(cases) == 30, f"behavior suite must contain 30 cases, found {len(cases)}", errors)
    require(dict(categories) == EXPECTED_CATEGORY_COUNTS, f"unexpected category distribution: {dict(categories)}", errors)
    require(tiers["smoke"] == 10, f"smoke tier must contain 10 cases, found {tiers['smoke']}", errors)
    require(risks["critical"] >= 10, "suite must contain at least 10 critical-risk cases", errors)

    return {
        "case_count": len(cases),
        "categories": dict(categories),
        "tiers": dict(tiers),
        "risks": dict(risks),
        "modes": dict(modes),
    }


def validate_trigger(payload: Any, errors: list[str]) -> dict[str, Any]:
    require(isinstance(payload, list), "trigger eval set must be an array", errors)
    if not isinstance(payload, list):
        return {}

    counts: Counter[bool] = Counter()
    queries: list[str] = []
    for index, case in enumerate(payload):
        prefix = f"trigger case #{index + 1}"
        require(isinstance(case, dict), f"{prefix} must be an object", errors)
        if not isinstance(case, dict):
            continue
        query = case.get("query")
        should_trigger = case.get("should_trigger")
        rationale = case.get("rationale")
        require(isinstance(query, str) and bool(query.strip()), f"{prefix} needs query", errors)
        require(type(should_trigger) is bool, f"{prefix} should_trigger must be boolean", errors)
        require(isinstance(rationale, str) and bool(rationale.strip()), f"{prefix} needs rationale", errors)
        if isinstance(query, str):
            queries.append(query)
        if type(should_trigger) is bool:
            counts[should_trigger] += 1

    require(len(queries) == len(set(queries)), "trigger queries must be unique", errors)
    require(len(payload) == 24, f"trigger suite must contain 24 cases, found {len(payload)}", errors)
    require(counts[True] == counts[False] == 12, f"trigger suite must be balanced 12/12, found {dict(counts)}", errors)
    return {"case_count": len(payload), "positive": counts[True], "negative": counts[False]}


def validate_grader_resources(behavior_payload: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    schema = load_json(RUN_SCHEMA_PATH)
    require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", "run result schema must use JSON Schema 2020-12", errors)
    required = schema.get("required", [])
    require(
        all(field in required for field in ("case_id", "run_id", "output", "tool_calls", "runtime")),
        "run result schema is missing required reproducibility fields",
        errors,
    )

    known_cases = {case["id"] for case in behavior_payload.get("cases", [])}
    fixture_rows: list[dict[str, Any]] = []
    with GRADER_FIXTURE_PATH.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"invalid grader fixture JSON at line {line_number}: {exc}")
                continue
            require(isinstance(row, dict), f"grader fixture line {line_number} must be an object", errors)
            if isinstance(row, dict):
                fixture_rows.append(row)

    run_ids = [row.get("run_id") for row in fixture_rows]
    require(len(run_ids) == len(set(run_ids)), "grader fixture run_ids must be unique", errors)
    for row in fixture_rows:
        require(row.get("case_id") in known_cases, f"grader fixture references unknown case {row.get('case_id')!r}", errors)
        require(isinstance(row.get("runtime"), dict), f"grader fixture {row.get('run_id')!r} needs runtime", errors)
        require(isinstance(row.get("tool_calls"), list), f"grader fixture {row.get('run_id')!r} needs tool_calls", errors)
    return {"schema_version": "2020-12", "fixture_runs": len(fixture_rows)}


def estimate_scale(case_count: int, repeats: int, correlation: float, average_tokens: int | None) -> dict[str, Any]:
    total_runs = case_count * repeats
    independent_moe = 1.96 * math.sqrt(0.25 / total_runs)
    effective_n = total_runs / (1 + (repeats - 1) * correlation)
    clustered_moe = 1.96 * math.sqrt(0.25 / effective_n)
    result: dict[str, Any] = {
        "unique_cases": case_count,
        "repeats_per_case": repeats,
        "model_runs": total_runs,
        "worst_case_95pct_moe_if_independent": round(independent_moe, 4),
        "assumed_intra_case_correlation": correlation,
        "effective_sample_size": round(effective_n, 2),
        "cluster_adjusted_worst_case_95pct_moe": round(clustered_moe, 4),
    }
    if average_tokens is not None:
        result["estimated_total_tokens"] = total_runs * average_tokens
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("smoke", "standard"), default="standard")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--intra-case-correlation", type=float, default=0.5)
    parser.add_argument("--average-tokens", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats must be >= 1")
    if not 0 <= args.intra_case_correlation <= 1:
        raise SystemExit("--intra-case-correlation must be between 0 and 1")
    if args.average_tokens is not None and args.average_tokens < 1:
        raise SystemExit("--average-tokens must be >= 1")

    errors: list[str] = []
    behavior_payload = load_json(BEHAVIOR_PATH)
    trigger_payload = load_json(TRIGGER_PATH)
    behavior_summary = validate_behavior(behavior_payload, errors)
    trigger_summary = validate_trigger(trigger_payload, errors)
    grader_summary = validate_grader_resources(behavior_payload, errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    selected_cases = 10 if args.suite == "smoke" else behavior_summary["case_count"]
    summary = {
        "status": "ok",
        "behavior": behavior_summary,
        "trigger": trigger_summary,
        "grader": grader_summary,
        "selected_suite": args.suite,
        "scale": estimate_scale(selected_cases, args.repeats, args.intra_case_correlation, args.average_tokens),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
