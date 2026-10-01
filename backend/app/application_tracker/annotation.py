"""Local baseline labels built from private tracker snapshots."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.application_tracker.evaluation import EvaluationCase
from app.application_tracker.models import ApplicationStatus


def case_from_snapshot(
    snapshot_dir: Path,
    *,
    case_id: str,
    company: str,
    role: str,
    expected_status: ApplicationStatus,
    expected_role: str | None,
    expected_applied_at: date | None,
) -> EvaluationCase:
    metadata = json.loads((snapshot_dir / "metadata.json").read_text(encoding="utf-8"))
    body_text = (snapshot_dir / "body.txt").read_text(encoding="utf-8")
    if len(body_text) > 500_000:
        raise ValueError("Snapshot body exceeds OfflineExtractionCase's 500,000-character limit")
    return EvaluationCase(
        case_id=case_id,
        company=company,
        role=role,
        url=metadata["final_url"],
        page_text=body_text,
        expected_status=expected_status,
        expected_role=expected_role,
        expected_applied_at=expected_applied_at,
    )


def write_cases(path: Path, cases: list[EvaluationCase]) -> None:
    """Atomically write labels to a path the caller has already checked is private."""
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case_id in labels")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(case.model_dump(mode="json"), ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    temporary.replace(path)
