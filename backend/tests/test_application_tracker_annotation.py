import json
from datetime import date
from pathlib import Path

import pytest

from app.application_tracker.annotation import case_from_snapshot, write_cases
from app.application_tracker.io import load_evaluation_cases
from app.application_tracker.models import ApplicationStatus


def test_annotation_reuses_saved_body_and_writes_valid_private_jsonl(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot-1"
    snapshot.mkdir()
    (snapshot / "metadata.json").write_text(json.dumps({"final_url": "https://careers.example.com/applications/1"}), encoding="utf-8")
    (snapshot / "body.txt").write_text("AI 产品经理\n当前状态：测评", encoding="utf-8")
    case = case_from_snapshot(
        snapshot,
        case_id="snapshot-1-01",
        company="示例公司",
        role="待识别岗位",
        expected_status=ApplicationStatus.ASSESSMENT,
        expected_role="AI 产品经理",
        expected_applied_at=date(2026, 9, 1),
    )
    labels = tmp_path / "labels.jsonl"
    write_cases(labels, [case])
    loaded = load_evaluation_cases(labels)
    assert loaded[0].expected_role == "AI 产品经理"
    assert loaded[0].expected_applied_at == date(2026, 9, 1)
    assert loaded[0].page_text == "AI 产品经理\n当前状态：测评"


def test_annotation_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot-1"
    snapshot.mkdir()
    (snapshot / "metadata.json").write_text(json.dumps({"final_url": "https://careers.example.com/applications/1"}), encoding="utf-8")
    (snapshot / "body.txt").write_text("当前状态：未知", encoding="utf-8")
    case = case_from_snapshot(
        snapshot,
        case_id="snapshot-1-01",
        company="示例公司",
        role="AI 产品经理",
        expected_status=ApplicationStatus.UNKNOWN,
        expected_role=None,
        expected_applied_at=None,
    )
    with pytest.raises(ValueError, match="Duplicate case_id"):
        write_cases(tmp_path / "labels.jsonl", [case, case])
