from types import SimpleNamespace

import pytest

from app.application_tracker.evaluation import EvaluationCase, evaluate_cases
from app.application_tracker.extractor import StatusExtractor
from app.application_tracker.models import ApplicationStatus, CheckResult, DiscoveredApplication, StatusRecord


class StubExtractor:
    def __init__(self, records: list[StatusRecord]) -> None:
        self._records = iter(records)

    def extract(self, *_args, **_kwargs) -> StatusRecord:
        return next(self._records)


def _record(status: ApplicationStatus, *, evidence: str, result: CheckResult = CheckResult.SUCCESS) -> StatusRecord:
    return StatusRecord(
        company="示例科技",
        role="AI 产品经理",
        url="https://careers.example.com/applications/123",
        status=status,
        raw_status=evidence,
        confidence=0.9,
        evidence=evidence,
        checked_at="2026-09-25T01:00:00Z",
        changed_at="2026-09-25T01:00:00Z",
        check_result=result,
    )


def test_evaluation_reports_status_accuracy_and_grounding() -> None:
    cases = [
        EvaluationCase(
            case_id="correct",
            company="示例科技",
            role="AI 产品经理",
            url="https://careers.example.com/applications/123",
            page_text="当前状态：测评",
            expected_status=ApplicationStatus.ASSESSMENT,
        ),
        EvaluationCase(
            case_id="wrong",
            company="另一家公司",
            role="产品实习生",
            url="https://jobs.example.org/applications/456",
            page_text="当前状态：一面",
            expected_status=ApplicationStatus.FIRST_INTERVIEW,
        ),
    ]
    extractor = StubExtractor(
        [
            _record(ApplicationStatus.ASSESSMENT, evidence="当前状态：测评"),
            _record(ApplicationStatus.RESUME_SCREENING, evidence="当前状态：一面"),
        ]
    )

    report = evaluate_cases(cases, extractor)

    assert report.total == 2
    assert report.status_correct == 1
    assert report.status_accuracy == 0.5
    assert report.evidence_grounded == 2
    assert report.schema_valid == 2
    assert [result.case_id for result in report.results] == ["correct", "wrong"]


def test_evaluation_reports_unknown_and_verbatim_evidence_with_explicit_denominator() -> None:
    cases = [
        EvaluationCase(
            case_id="known",
            company="示例科技",
            role="待识别岗位",
            url="https://careers.example.com/applications/1",
            page_text="当前状态：测评",
            expected_status=ApplicationStatus.ASSESSMENT,
            expected_role="AI 产品经理",
            expected_applied_at=None,
        ),
        EvaluationCase(
            case_id="unknown",
            company="示例科技",
            role="待识别岗位",
            url="https://careers.example.com/applications/2",
            page_text="尚无进度信息",
            expected_status=ApplicationStatus.UNKNOWN,
            expected_applied_at=None,
        ),
    ]
    known = _record(ApplicationStatus.ASSESSMENT, evidence="当前状态：测评").model_copy(update={"role": "待识别岗位", "detected_role": "AI 产品经理"})
    unknown = _record(ApplicationStatus.UNKNOWN, evidence="")
    report = evaluate_cases(cases, StubExtractor([known, unknown]))
    assert report.unknown_rate == 0.5
    assert report.verbatim_evidence_eligible == 1
    assert report.verbatim_evidence_rate == 1.0
    assert report.role_accuracy == 1.0
    assert report.applied_at_accuracy == 1.0
    assert report.mean_elapsed_ms is not None
    assert report.total_input_tokens is None
    assert report.total_cost_usd is None


class FakeUsageModel:
    def invoke(self, _messages, **kwargs):
        for callback in kwargs["config"]["callbacks"]:
            callback.on_llm_end(SimpleNamespace(generations=[], llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}}))
        return {
            "status": "测评",
            "raw_status": "测评",
            "confidence": 0.9,
            "evidence": "当前状态：测评",
        }


def test_evaluation_uses_reported_tokens_and_explicit_prices() -> None:
    case = EvaluationCase(
        case_id="metered",
        company="示例科技",
        role="AI 产品经理",
        url="https://careers.example.com/applications/1",
        page_text="当前状态：测评",
        expected_status=ApplicationStatus.ASSESSMENT,
    )
    report = evaluate_cases(
        [case],
        StatusExtractor(FakeUsageModel()),
        input_usd_per_million=1.0,
        output_usd_per_million=2.0,
    )
    assert report.total_input_tokens == 10
    assert report.total_output_tokens == 5
    assert report.total_cost_usd == pytest.approx(0.00002)
    assert report.token_coverage == 1.0


def test_evaluation_rejects_one_sided_token_pricing() -> None:
    with pytest.raises(ValueError, match="Both input and output"):
        evaluate_cases([], StubExtractor([]), input_usd_per_million=1.0)


def test_failed_unknown_record_does_not_count_as_correct_status() -> None:
    case = EvaluationCase(
        case_id="failed",
        company="示例科技",
        role="AI 产品经理",
        url="https://careers.example.com/applications/1",
        page_text="无法加载",
        expected_status=ApplicationStatus.UNKNOWN,
        expected_applied_at=None,
    )
    failed = _record(ApplicationStatus.UNKNOWN, evidence="", result=CheckResult.FETCH_FAILED)
    report = evaluate_cases([case], StubExtractor([failed]))
    assert report.status_accuracy == 0
    assert report.applied_at_accuracy == 0
    assert report.unknown_rate == 1


def test_listing_case_scores_only_the_matching_application() -> None:
    case = EvaluationCase(
        case_id="listing-role-b",
        company="示例科技",
        role="岗位 B",
        url="https://careers.example.com/applications",
        page_text="岗位 A：Offer\n岗位 B：测评",
        expected_status=ApplicationStatus.ASSESSMENT,
        expected_applied_at=None,
    )
    record = _record(ApplicationStatus.OFFER, evidence="岗位 A：Offer").model_copy(
        update={
            "discovered_applications": [
                DiscoveredApplication(
                    role="岗位 A",
                    role_evidence="岗位 A：Offer",
                    status=ApplicationStatus.OFFER,
                    raw_status="Offer",
                    confidence=0.9,
                    evidence="岗位 A：Offer",
                ),
                DiscoveredApplication(
                    role="岗位 B",
                    role_evidence="岗位 B：测评",
                    status=ApplicationStatus.ASSESSMENT,
                    raw_status="测评",
                    confidence=0.9,
                    evidence="岗位 B：测评",
                ),
            ]
        }
    )
    missing = case.model_copy(update={"case_id": "listing-role-c", "role": "岗位 C", "expected_status": ApplicationStatus.UNKNOWN})
    report = evaluate_cases([case, missing], StubExtractor([record, record]))
    assert report.status_accuracy == 1
    assert report.verbatim_evidence_rate == 1
    assert report.results[1].predicted_status is ApplicationStatus.UNKNOWN
