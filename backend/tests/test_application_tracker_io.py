import csv
import json
from pathlib import Path

import pytest

from app.application_tracker.io import load_evaluation_cases, write_status_csv
from app.application_tracker.models import ApplicationStatus, CheckResult, StatusRecord


def test_load_evaluation_cases_reports_invalid_jsonl_line(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    valid = {
        "case_id": "ok",
        "company": "示例科技",
        "role": "AI 产品经理",
        "url": "https://careers.example.com/applications/123",
        "page_text": "当前状态：测评",
        "expected_status": "测评",
    }
    path.write_text(json.dumps(valid, ensure_ascii=False) + "\nnot-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="line 2"):
        load_evaluation_cases(path)


def test_write_status_csv_uses_the_public_output_contract(tmp_path: Path) -> None:
    output = tmp_path / "results.csv"
    record = StatusRecord(
        company="示例科技",
        role="AI 产品经理",
        url="https://careers.example.com/applications/123",
        status=ApplicationStatus.OFFER,
        raw_status="Offer 已发放",
        confidence=0.98,
        evidence="当前状态：Offer 已发放",
        checked_at="2026-09-25T01:00:00Z",
        changed_at="2026-09-25T01:00:00Z",
        check_result=CheckResult.SUCCESS,
    )

    write_status_csv(output, [record])

    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == [
        "company",
        "role",
        "url",
        "status",
        "raw_status",
        "confidence",
        "evidence",
        "checked_at",
        "changed_at",
        "check_result",
    ]
    assert rows[0]["status"] == "Offer"
    assert rows[0]["check_result"] == "成功"


def test_committed_eval_fixture_covers_every_status() -> None:
    fixture = Path(__file__).parent / "fixtures" / "application_tracker" / "eval_cases.jsonl"
    cases = load_evaluation_cases(fixture)

    assert {case.expected_status for case in cases} == set(ApplicationStatus)
    assert len({case.case_id for case in cases}) == len(cases)
    assert all("example" in case.url for case in cases)
