"""Small deterministic grader for offline page-text extraction runs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from statistics import mean
from time import perf_counter
from typing import Protocol

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel, ConfigDict, Field

from app.application_tracker.adapters import try_adapters
from app.application_tracker.extractor import StatusExtractor, is_evidence_grounded
from app.application_tracker.models import ApplicationInput, ApplicationStatus, CheckResult, OfflineExtractionCase, StatusRecord


class EvaluationCase(OfflineExtractionCase):
    expected_status: ApplicationStatus
    expected_role: str | None = None
    expected_applied_at: date | None = None
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
    role_correct: bool | None = None
    applied_at_correct: bool | None = None
    evidence_eligible: bool = False
    elapsed_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    record: StatusRecord | None = None
    error: str | None = Field(default=None, max_length=500)
    adapter: str | None = None
    adapter_fallback: str | None = None


class AdapterMetrics(BaseModel):
    """Accuracy denominator is accepted cases; coverage denominator is all cases."""

    accepted: int
    coverage: float
    status_accuracy: float | None
    fallback_reasons: dict[str, int]
    by_adapter: dict[str, dict[str, int | float | None]]


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
    unknown_count: int
    unknown_rate: float
    verbatim_evidence_eligible: int
    verbatim_evidence_grounded: int
    verbatim_evidence_rate: float | None
    mean_elapsed_ms: float | None
    token_coverage: float
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_cost_usd: float | None = None
    role_accuracy: float | None = None
    applied_at_accuracy: float | None = None
    results: list[EvaluationCaseResult]
    adapter_metrics: AdapterMetrics | None = None


class Extractor(Protocol):
    def extract(self, application: ApplicationInput, page_text: str, **kwargs) -> StatusRecord: ...


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls_with_usage: int = 0


class TokenUsageCollector(BaseCallbackHandler):
    """Read provider-reported usage; never infer tokens from text length."""

    def __init__(self) -> None:
        self.usage = TokenUsage()

    def on_llm_end(self, response, **kwargs) -> None:  # type: ignore[override]
        token_usage = None
        for group in getattr(response, "generations", []) or []:
            for generation in group:
                token_usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if token_usage:
                    break
            if token_usage:
                break
        if not token_usage:
            llm_output = getattr(response, "llm_output", None)
            token_usage = llm_output.get("token_usage") if isinstance(llm_output, dict) else None
        if not isinstance(token_usage, dict):
            return
        input_tokens = token_usage.get("input_tokens", token_usage.get("prompt_tokens"))
        output_tokens = token_usage.get("output_tokens", token_usage.get("completion_tokens"))
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            return
        self.usage.input_tokens += input_tokens
        self.usage.output_tokens += output_tokens
        self.usage.calls_with_usage += 1


def evaluate_cases(
    cases: list[EvaluationCase],
    extractor: Extractor | None = None,
    *,
    adapters_only: bool = False,
    input_usd_per_million: float | None = None,
    output_usd_per_million: float | None = None,
) -> EvaluationReport:
    if not adapters_only and extractor is None:
        raise ValueError("An extractor is required unless adapters_only=True")
    if (input_usd_per_million is None) != (output_usd_per_million is None):
        raise ValueError("Both input and output token prices must be supplied together")
    if input_usd_per_million is not None and (input_usd_per_million < 0 or output_usd_per_million < 0):
        raise ValueError("Token prices must be non-negative")
    results: list[EvaluationCaseResult] = []
    for case in cases:
        application = ApplicationInput.model_validate(case.model_dump(include=set(ApplicationInput.model_fields)))
        collector = TokenUsageCollector()
        started = perf_counter()
        decision = None
        try:
            if adapters_only:
                now = datetime.now(UTC)
                decision = try_adapters(application, case.json_responses, checked_at=now)
                # Missing adapter coverage is unknown here. Production alone invokes fallback.
                extracted = decision.record or StatusRecord(
                    company=application.company, role=application.role, url=application.url, status=ApplicationStatus.UNKNOWN, confidence=0, checked_at=now, changed_at=now, check_result=CheckResult.SUCCESS
                )
            elif isinstance(extractor, StatusExtractor):
                extracted = extractor.extract(application, case.page_text, callbacks=[collector], observations=case.observations)
            else:
                extracted = extractor.extract(application, case.page_text)
            record = extracted if isinstance(extracted, StatusRecord) else StatusRecord.model_validate(extracted)
            schema_valid = True
            error = None
        except Exception as exc:
            record = None
            schema_valid = False
            error = type(exc).__name__  # Validation errors may contain private snapshot data.
        elapsed_ms = (perf_counter() - started) * 1000

        selected_application = None
        if record and record.discovered_applications:
            target_role = case.expected_role if case.role == "待识别岗位" else case.role
            selected_application = next((item for item in record.discovered_applications if item.role == target_role), None)
        if record is None:
            predicted_status = None
            evidence = ""
        elif record.discovered_applications:
            predicted_status = selected_application.status if selected_application is not None else ApplicationStatus.UNKNOWN
            evidence = selected_application.evidence if selected_application is not None else ""
        else:
            predicted_status = record.status
            evidence = record.evidence
        allow_empty = predicted_status is ApplicationStatus.UNKNOWN
        source_texts = [case.page_text, *(item.text for item in case.observations)]
        if adapters_only:
            source_texts.extend(item.evidence_text() for item in case.json_responses)
        grounded = bool(record) and any(is_evidence_grounded(text, evidence, allow_empty=allow_empty) for text in source_texts)
        evidence_eligible = bool(record) and (predicted_status is not ApplicationStatus.UNKNOWN or bool(evidence))
        usage = collector.usage if collector.usage.calls_with_usage else None
        if adapters_only:
            usage = TokenUsage()  # This branch has no model call or fallback invocation.
        cost = 0.0 if adapters_only else None
        if usage is not None and input_usd_per_million is not None:
            cost = (usage.input_tokens * input_usd_per_million + usage.output_tokens * output_usd_per_million) / 1_000_000
        role_correct = None
        if case.role == "待识别岗位" and case.expected_role is not None:
            role_correct = bool(record) and record.check_result is CheckResult.SUCCESS and (selected_application is not None or record.detected_role == case.expected_role)
        applied_at_correct = None
        if "expected_applied_at" in case.model_fields_set:
            observed_date = None
            if selected_application is not None and selected_application.applied_at:
                try:
                    observed_date = date.fromisoformat(selected_application.applied_at)
                except ValueError:
                    pass
            elif record is not None and not record.discovered_applications:
                observed_date = record.applied_at
            applied_at_correct = bool(record) and record.check_result is CheckResult.SUCCESS and observed_date == case.expected_applied_at
        results.append(
            EvaluationCaseResult(
                case_id=case.case_id,
                expected_status=case.expected_status,
                predicted_status=predicted_status,
                status_correct=bool(record) and record.check_result is case.expected_check_result and predicted_status is case.expected_status,
                schema_valid=schema_valid,
                evidence_grounded=grounded,
                check_result_correct=bool(record) and record.check_result is case.expected_check_result,
                role_correct=role_correct,
                applied_at_correct=applied_at_correct,
                evidence_eligible=evidence_eligible,
                elapsed_ms=elapsed_ms,
                input_tokens=usage.input_tokens if usage is not None else None,
                output_tokens=usage.output_tokens if usage is not None else None,
                cost_usd=cost,
                record=record,
                error=error,
                adapter=decision.adapter if decision else None,
                adapter_fallback=decision.reason if decision else None,
            )
        )

    total = len(results)
    schema_valid_count = sum(result.schema_valid for result in results)
    status_correct_count = sum(result.status_correct for result in results)
    grounded_count = sum(result.evidence_grounded for result in results)
    check_result_count = sum(result.check_result_correct for result in results)
    unknown_count = sum(result.predicted_status is ApplicationStatus.UNKNOWN for result in results)
    evidence_eligible_count = sum(result.evidence_eligible for result in results)
    verbatim_count = sum(result.evidence_eligible and result.evidence_grounded for result in results)
    usage_complete = total > 0 and all(result.input_tokens is not None and result.output_tokens is not None for result in results)
    role_results = [result.role_correct for result in results if result.role_correct is not None]
    date_results = [result.applied_at_correct for result in results if result.applied_at_correct is not None]
    denominator = total or 1
    adapter_metrics = None
    if adapters_only:
        accepted = [result for result in results if result.adapter and not result.adapter_fallback and result.schema_valid]
        by_adapter = {}
        for name in sorted({result.adapter for result in results if result.adapter}):
            selected = [result for result in results if result.adapter == name]
            hits = [result for result in accepted if result.adapter == name]
            by_adapter[name] = {
                "total": len(selected),
                "accepted": len(hits),
                "correct": sum(result.status_correct for result in hits),
                "coverage": len(hits) / len(selected),
                "status_accuracy": sum(result.status_correct for result in hits) / len(hits) if hits else None,
            }
        adapter_metrics = AdapterMetrics(
            accepted=len(accepted),
            coverage=len(accepted) / denominator,
            status_accuracy=sum(result.status_correct for result in accepted) / len(accepted) if accepted else None,
            fallback_reasons=dict(Counter(result.adapter_fallback or "adapter_error" for result in results if result not in accepted)),
            by_adapter=by_adapter,
        )
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
        unknown_count=unknown_count,
        unknown_rate=unknown_count / denominator,
        verbatim_evidence_eligible=evidence_eligible_count,
        verbatim_evidence_grounded=verbatim_count,
        verbatim_evidence_rate=verbatim_count / evidence_eligible_count if evidence_eligible_count else None,
        mean_elapsed_ms=mean(result.elapsed_ms for result in results) if results else None,
        token_coverage=sum(result.input_tokens is not None for result in results) / denominator,
        total_input_tokens=sum(result.input_tokens for result in results if result.input_tokens is not None) if usage_complete else None,
        total_output_tokens=sum(result.output_tokens for result in results if result.output_tokens is not None) if usage_complete else None,
        total_cost_usd=sum(result.cost_usd for result in results if result.cost_usd is not None) if usage_complete and (adapters_only or input_usd_per_million is not None) else None,
        role_accuracy=sum(role_results) / len(role_results) if role_results else None,
        applied_at_accuracy=sum(date_results) / len(date_results) if date_results else None,
        results=results,
        adapter_metrics=adapter_metrics,
    )
