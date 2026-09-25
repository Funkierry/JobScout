from app.application_tracker.evaluation import EvaluationCase, evaluate_cases
from app.application_tracker.models import ApplicationStatus, CheckResult, StatusRecord


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
