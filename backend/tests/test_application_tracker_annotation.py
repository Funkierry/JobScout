import json
from datetime import date
from pathlib import Path

import pytest

from app.application_tracker.annotation import attach_snapshot_observations, case_from_snapshot, write_cases
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


def test_replay_attaches_json_without_mutating_labels_or_using_expected_status(tmp_path):
    snapshot = tmp_path / "snapshot-1"
    snapshot.mkdir()
    (snapshot / "metadata.json").write_text(json.dumps({"final_url": "https://jobs.example/applications"}), encoding="utf-8")
    (snapshot / "body.txt").write_text("产品经理 当前状态：已投递", encoding="utf-8")
    case = case_from_snapshot(snapshot, case_id="one", company="Example", role="产品经理", expected_status=ApplicationStatus.APPLIED, expected_role="产品经理", expected_applied_at=None)
    (snapshot / "responses.json").write_text(json.dumps([{"url": "https://jobs.example/api", "status": 200, "body": {"role": "产品经理", "status": "已投递"}}]), encoding="utf-8")
    attached = attach_snapshot_observations([case], tmp_path)
    assert not case.observations and len(attached[0].observations) == 1
    assert not case.json_responses and len(attached[0].json_responses) == 1
    assert "expected_status" not in attached[0].json_responses[0].model_dump_json()
    assert "expected_status" not in attached[0].observations[0].text
    # Duplicate captures must not be chosen arbitrarily.
    second = tmp_path / "snapshot-2"
    second.mkdir()
    for name in ["metadata.json", "body.txt"]:
        (second / name).write_bytes((snapshot / name).read_bytes())
    with pytest.raises(ValueError, match="Ambiguous"):
        attach_snapshot_observations([case], tmp_path)
