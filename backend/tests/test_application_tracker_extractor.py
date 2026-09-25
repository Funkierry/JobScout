from datetime import UTC, datetime

from langchain_core.messages import HumanMessage, SystemMessage

from app.application_tracker.extractor import StatusExtractor
from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
    StatusExtraction,
    StatusRecord,
)


class StubStructuredModel:
    def __init__(self, response: StatusExtraction | dict | Exception) -> None:
        self.response = response
        self.calls: list[list[SystemMessage | HumanMessage]] = []

    def invoke(self, messages: list[SystemMessage | HumanMessage]) -> StatusExtraction | dict:
        self.calls.append(messages)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _application() -> ApplicationInput:
    return ApplicationInput(
        company="示例科技",
        role="AI 产品经理",
        url="https://careers.example.com/applications/123",
        applied_at="2026-09-01",
        notes="校招",
    )


def test_extracts_grounded_status_and_marks_page_as_untrusted() -> None:
    model = StubStructuredModel(
        StatusExtraction(
            status=ApplicationStatus.WRITTEN_TEST,
            raw_status="笔试环节",
            confidence=0.93,
            evidence="当前申请进度：笔试环节",
        )
    )
    extractor = StatusExtractor(model)
    checked_at = datetime(2026, 9, 25, 2, 30, tzinfo=UTC)

    result = extractor.extract(
        _application(),
        "岗位：AI 产品经理\n当前申请进度：笔试环节\n请在三天内完成。",
        checked_at=checked_at,
    )

    assert result.status is ApplicationStatus.WRITTEN_TEST
    assert result.check_result is CheckResult.SUCCESS
    assert result.checked_at == checked_at
    assert result.changed_at == checked_at
    assert len(model.calls) == 1
    assert "不可信页面数据" in str(model.calls[0][0].content)
    assert "<page_text>" in str(model.calls[0][1].content)


def test_normalized_whitespace_evidence_is_recovered_from_original_page() -> None:
    model = StubStructuredModel(
        {
            "status": "二面",
            "raw_status": "第二轮面试",
            "confidence": 0.88,
            "evidence": "当前阶段 第二轮面试",
        }
    )
    extractor = StatusExtractor(model)

    result = extractor.extract(_application(), "当前阶段\n\t第二轮面试\n面试时间待确认")

    assert result.status is ApplicationStatus.SECOND_INTERVIEW
    assert result.evidence == "当前阶段\n\t第二轮面试"


def test_ungrounded_model_output_degrades_to_failed_unknown_record() -> None:
    model = StubStructuredModel(
        StatusExtraction(
            status=ApplicationStatus.OFFER,
            raw_status="Offer 已发放",
            confidence=0.99,
            evidence="恭喜你，Offer 已发放",
        )
    )
    extractor = StatusExtractor(model)

    result = extractor.extract(_application(), "当前申请进度：简历筛选中")

    assert result.status is ApplicationStatus.UNKNOWN
    assert result.check_result is CheckResult.FETCH_FAILED
    assert result.confidence == 0
    assert result.raw_status == ""
    assert result.evidence == ""


def test_empty_page_does_not_call_model() -> None:
    model = StubStructuredModel(RuntimeError("must not be called"))
    extractor = StatusExtractor(model)

    result = extractor.extract(_application(), "  \n  ")

    assert result.status is ApplicationStatus.UNKNOWN
    assert result.check_result is CheckResult.FETCH_FAILED
    assert model.calls == []


def test_changed_at_is_preserved_when_status_does_not_change() -> None:
    original_changed_at = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)
    previous = StatusRecord(
        company="示例科技",
        role="AI 产品经理",
        url="https://careers.example.com/applications/123",
        status=ApplicationStatus.RESUME_SCREENING,
        raw_status="简历筛选中",
        confidence=0.91,
        evidence="当前状态：简历筛选中",
        checked_at=datetime(2026, 9, 20, 1, 0, tzinfo=UTC),
        changed_at=original_changed_at,
        check_result=CheckResult.SUCCESS,
    )
    model = StubStructuredModel(
        StatusExtraction(
            status=ApplicationStatus.RESUME_SCREENING,
            raw_status="简历筛选中",
            confidence=0.92,
            evidence="当前状态：简历筛选中",
        )
    )
    extractor = StatusExtractor(model)

    result = extractor.extract(
        _application(),
        "当前状态：简历筛选中",
        previous=previous,
        checked_at=datetime(2026, 9, 25, 1, 0, tzinfo=UTC),
    )

    assert result.changed_at == original_changed_at


def test_model_failure_does_not_abort_the_row() -> None:
    extractor = StatusExtractor(StubStructuredModel(RuntimeError("provider unavailable")))

    result = extractor.extract(_application(), "当前状态：测评")

    assert result.status is ApplicationStatus.UNKNOWN
    assert result.check_result is CheckResult.FETCH_FAILED
