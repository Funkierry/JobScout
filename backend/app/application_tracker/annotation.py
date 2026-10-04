"""Local baseline labels built from private tracker snapshots."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.application_tracker.evaluation import EvaluationCase
from app.application_tracker.models import ApplicationStatus
from app.application_tracker.observations import captured_json_responses, json_observations


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
        observations=json_observations(json.loads((snapshot_dir / "responses.json").read_text(encoding="utf-8")), page_url=metadata["final_url"]) if (snapshot_dir / "responses.json").exists() else [],
        json_responses=captured_json_responses(json.loads((snapshot_dir / "responses.json").read_text(encoding="utf-8")), page_url=metadata["final_url"]) if (snapshot_dir / "responses.json").exists() else [],
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


def attach_snapshot_observations(cases: list[EvaluationCase], snapshot_root: Path) -> list[EvaluationCase]:
    """Attach capture JSON to frozen labels in memory; never modify the labels.

    Exact body + sanitized final URL matching avoids relying on private case-id
    naming conventions. Ambiguous captures require explicit observations in labels.
    """
    import hashlib

    from app.application_tracker.redaction import sanitize_url

    snapshots: dict[tuple[str, str], list[Path]] = {}
    for path in sorted(snapshot_root.glob("*/body.txt")):
        if path.stat().st_size > 2_000_000 or not path.resolve().is_relative_to(snapshot_root.resolve()):
            continue
        metadata_path = path.parent / "metadata.json"
        if not metadata_path.exists():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        body = path.read_text(encoding="utf-8")
        key = (hashlib.sha256(body.encode()).hexdigest(), sanitize_url(metadata["final_url"]))
        snapshots.setdefault(key, []).append(path.parent)
    result = []
    for case in cases:
        key = (hashlib.sha256(case.page_text.encode()).hexdigest(), sanitize_url(case.url))
        matches = snapshots.get(key, [])
        if (not case.observations or not case.json_responses) and len(matches) > 1:
            raise ValueError("Ambiguous snapshot match; explicitly label a single capture before replay")
        if (not case.observations or not case.json_responses) and matches:
            path = matches[0] / "responses.json"
            if path.exists() and path.stat().st_size <= 26_000_000 and path.resolve().is_relative_to(snapshot_root.resolve()):
                responses = json.loads(path.read_text(encoding="utf-8"))
                observations = case.observations or json_observations(responses, page_url=case.url)
                captured = case.json_responses or captured_json_responses(responses, page_url=case.url)
                case = case.model_copy(update={"observations": observations, "json_responses": captured})
        result.append(case)
    return result
