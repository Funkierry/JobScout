"""Small deterministic grader for offline page-text extraction runs."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.application_tracker.extractor import is_evidence_grounded
from app.application_tracker.models import ApplicationInput, ApplicationStatus, CheckResult, OfflineExtractionCase, StatusRecord


class EvaluationCase(OfflineExtractionCase):
    expected_status: ApplicationStatus
    expected_check_result: CheckResult = CheckResult.SUCCESS


class EvaluationCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    expected_status: ApplicationStatus
    predicted_status: ApplicationStatus | None
    status_correct: bool
    schema_valid: bool
    evidence_grounded: bool
    check_result_correct: bool
    record: StatusRecord | None = None
    error: str | None = Field(default=None, max_length=500)


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    schema_valid: int
    status_correct: int
    evidence_grounded: int
    check_result_correct: int
    status_accuracy: float
    schema_valid_rate: float
    evidence_grounded_rate: float
    check_result_accuracy: float
    results: list[EvaluationCaseResult]


class Extractor(Protocol):
    def extract(self, application: ApplicationInput, page_text: str, **kwargs) -> StatusRecord: ...


def evaluate_cases(cases: list[EvaluationCase], extractor: Extractor) -> EvaluationReport:
    results: list[EvaluationCaseResult] = []
    for case in cases:
        application = ApplicationInput.model_validate(case.model_dump(include=set(ApplicationInput.model_fields)))
        try:
            extracted = extractor.extract(application, case.page_text)
            record = extracted if isinstance(extracted, StatusRecord) else StatusRecord.model_validate(extracted)
            schema_valid = True
            error = None
        except Exception as exc:
            record = None
            schema_valid = False
            error = f"{type(exc).__name__}: {exc}"[:500]

        predicted_status = record.status if record else None
        allow_empty = predicted_status is ApplicationStatus.UNKNOWN
        grounded = bool(record) and is_evidence_grounded(case.page_text, record.evidence, allow_empty=allow_empty)
        results.append(
            EvaluationCaseResult(
                case_id=case.case_id,
                expected_status=case.expected_status,
                predicted_status=predicted_status,
                status_correct=predicted_status is case.expected_status,
                schema_valid=schema_valid,
                evidence_grounded=grounded,
                check_result_correct=bool(record) and record.check_result is case.expected_check_result,
                record=record,
                error=error,
            )
        )

    total = len(results)
    schema_valid_count = sum(result.schema_valid for result in results)
    status_correct_count = sum(result.status_correct for result in results)
    grounded_count = sum(result.evidence_grounded for result in results)
    check_result_count = sum(result.check_result_correct for result in results)
    denominator = total or 1
    return EvaluationReport(
        total=total,
        schema_valid=schema_valid_count,
        status_correct=status_correct_count,
        evidence_grounded=grounded_count,
        check_result_correct=check_result_count,
        status_accuracy=status_correct_count / denominator,
        schema_valid_rate=schema_valid_count / denominator,
        evidence_grounded_rate=grounded_count / denominator,
        check_result_accuracy=check_result_count / denominator,
        results=results,
    )
