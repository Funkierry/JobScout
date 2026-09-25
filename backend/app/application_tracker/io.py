"""UTF-8 JSONL inputs and CSV/JSON outputs for the offline workflow."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.application_tracker.evaluation import EvaluationCase, EvaluationReport
from app.application_tracker.models import OfflineExtractionCase, StatusRecord

STATUS_CSV_FIELDS = [
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


def _load_jsonl[ModelT: BaseModel](path: Path, model: type[ModelT]) -> list[ModelT]:
    rows: list[ModelT] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload: Any = json.loads(line)
                rows.append(model.model_validate(payload))
            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                raise ValueError(f"invalid {model.__name__} JSONL at line {line_number}: {exc}") from exc
    return rows


def load_offline_cases(path: Path) -> list[OfflineExtractionCase]:
    return _load_jsonl(path, OfflineExtractionCase)


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    return _load_jsonl(path, EvaluationCase)


def write_status_csv(path: Path, records: list[StatusRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATUS_CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(record.model_dump(mode="json"))


def write_evaluation_report(path: Path, report: EvaluationReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
