from datetime import UTC, date, datetime

from langchain_core.messages import HumanMessage, SystemMessage

from app.application_tracker.extractor import StatusExtractor
from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
    DiscoveredApplication,
    StatusExtraction,
    StatusRecord,
)
from app.application_tracker.status_semantics import normalize_generic_active_status


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


def test_generic_current_application_progress_is_grouped_as_screening() -> None:
    model = StubStructuredModel(
        StatusExtraction(
            status=ApplicationStatus.UNKNOWN,
            raw_status="进行中",
            confidence=0.91,
            evidence="当前申请进度：进行中",
        )
    )

    result = StatusExtractor(model).extract(_application(), "当前申请进度：进行中")

    assert result.status is ApplicationStatus.RESUME_SCREENING
    assert result.raw_status == "进行中"
    assert result.evidence == "当前申请进度：进行中"
    assert result.confidence == 0.75
    assert "进行中" in str(model.calls[0][0].content)
    assert "更具体环节" in str(model.calls[0][0].content)


def test_ongoing_wording_next_to_application_submission_is_grouped_as_screening() -> None:
    model = StubStructuredModel(
        StatusExtraction(
            status=ApplicationStatus.UNKNOWN,
            raw_status="进行中",
            confidence=0.9,
            evidence="欢迎投递示例集团-进行中",
        )
    )

    result = StatusExtractor(model).extract(_application(), "欢迎投递示例集团-进行中")

    assert result.status is ApplicationStatus.RESUME_SCREENING
    assert result.raw_status == "进行中"
    assert result.evidence == "欢迎投递示例集团-进行中"
    assert result.confidence == 0.75


def test_generic_progress_does_not_override_explicit_assessment() -> None:
    status, confidence = normalize_generic_active_status(
        ApplicationStatus.ASSESSMENT,
        raw_status="进行中",
        evidence="当前测评进度：进行中",
        confidence=0.94,
    )

    assert status is ApplicationStatus.ASSESSMENT
    assert confidence == 0.94


def test_unrelated_activity_is_not_classified_as_application_progress() -> None:
    status, confidence = normalize_generic_active_status(
        ApplicationStatus.UNKNOWN,
        raw_status="进行中",
        evidence="直播活动进行中",
        confidence=0.9,
    )

    assert status is ApplicationStatus.UNKNOWN
    assert confidence == 0.9


def test_termination_signal_blocks_generic_active_fallback() -> None:
    status, confidence = normalize_generic_active_status(
        ApplicationStatus.UNKNOWN,
        raw_status="进行中",
        evidence="当前申请进度：进行中；实际已终止",
        confidence=0.9,
    )

    assert status is ApplicationStatus.UNKNOWN
    assert confidence == 0.9


def test_specific_interview_wording_blocks_generic_active_fallback() -> None:
    status, confidence = normalize_generic_active_status(
        ApplicationStatus.UNKNOWN,
        raw_status="in progress",
        evidence="Application status: Interview in progress",
        confidence=0.9,
    )

    assert status is ApplicationStatus.UNKNOWN
    assert confidence == 0.9


def test_generic_phrase_must_be_part_of_its_application_evidence() -> None:
    status, confidence = normalize_generic_active_status(
        ApplicationStatus.UNKNOWN,
        raw_status="进行中",
        evidence="当前申请进度：待定",
        confidence=0.9,
    )

    assert status is ApplicationStatus.UNKNOWN
    assert confidence == 0.9


def test_generic_progress_is_normalized_for_each_discovered_role() -> None:
    model = StubStructuredModel(
        StatusExtraction(
            status=ApplicationStatus.UNKNOWN,
            confidence=0.4,
            discovered_applications=[
                DiscoveredApplication(
                    role="产品经理",
                    role_evidence="产品经理",
                    status=ApplicationStatus.UNKNOWN,
                    raw_status="进行中",
                    confidence=0.9,
                    evidence="产品经理 当前申请进度：进行中",
                ),
                DiscoveredApplication(
                    role="数据分析师",
                    role_evidence="数据分析师",
                    status=ApplicationStatus.REJECTED,
                    raw_status="未通过",
                    confidence=0.95,
                    evidence="数据分析师 当前状态：未通过",
                ),
            ],
        )
    )
    page_text = "产品经理 当前申请进度：进行中\n数据分析师 当前状态：未通过"

    result = StatusExtractor(model).extract(_application(), page_text)

    assert [item.status for item in result.discovered_applications] == [
        ApplicationStatus.RESUME_SCREENING,
        ApplicationStatus.REJECTED,
    ]
    assert result.discovered_applications[0].confidence == 0.75


def test_extracts_site_application_date_with_verbatim_evidence() -> None:
    model = StubStructuredModel(
        StatusExtraction(
            status=ApplicationStatus.RESUME_SCREENING,
            raw_status="筛选中",
            confidence=0.92,
            evidence="当前状态：筛选中",
            applied_at="2026-09-15",
            applied_at_evidence="投递时间：2026年9月15日 10:30",
        )
    )
    page_text = "投递时间：2026年9月15日 10:30\n当前状态：筛选中"

    result = StatusExtractor(model).extract(_application(), page_text)

    assert result.applied_at == date(2026, 9, 15)
    assert result.applied_at_evidence == "投递时间：2026年9月15日 10:30"
    assert "applied_at_evidence" in str(model.calls[0][0].content)


def test_discovered_roles_keep_separate_site_application_dates() -> None:
    model = StubStructuredModel(
        StatusExtraction(
            status=ApplicationStatus.UNKNOWN,
            confidence=0.4,
            discovered_applications=[
                DiscoveredApplication(
                    role="产品经理",
                    role_evidence="产品经理",
                    status=ApplicationStatus.APPLIED,
                    raw_status="已投递",
                    confidence=0.92,
                    evidence="当前状态：已投递",
                    applied_at="2026-09-10",
                    applied_at_evidence="产品经理 投递时间：2026-09-10",
                ),
                DiscoveredApplication(
                    role="数据分析师",
                    role_evidence="数据分析师",
                    status=ApplicationStatus.RESUME_SCREENING,
                    raw_status="筛选中",
                    confidence=0.9,
                    evidence="当前状态：筛选中",
                    applied_at="2026-09-12",
                    applied_at_evidence="数据分析师 投递时间：2026-09-12",
                ),
            ],
        )
    )
    page_text = "产品经理 投递时间：2026-09-10 当前状态：已投递\n数据分析师 投递时间：2026-09-12 当前状态：筛选中"

    result = StatusExtractor(model).extract(_application(), page_text)

    assert [item.applied_at for item in result.discovered_applications] == ["2026-09-10", "2026-09-12"]
