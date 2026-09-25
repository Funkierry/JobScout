from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
    StatusExtraction,
    StatusRecord,
)


def test_application_status_values_match_product_contract() -> None:
    assert [status.value for status in ApplicationStatus] == [
        "已投递",
        "简历筛选",
        "测评",
        "笔试",
        "一面",
        "二面",
        "三面",
        "HR面",
        "Offer",
        "未通过",
        "流程终止",
        "未知",
    ]


def test_application_input_accepts_only_http_urls() -> None:
    application = ApplicationInput(
        company="示例科技",
        role="AI 产品经理",
        url="https://careers.example.com/applications/123",
        applied_at=date(2026, 9, 1),
        notes="校招",
    )

    assert application.url == "https://careers.example.com/applications/123"

    with pytest.raises(ValidationError, match="HTTP or HTTPS"):
        ApplicationInput(
            company="示例科技",
            role="AI 产品经理",
            url="file:///tmp/application.html",
        )


def test_non_unknown_extraction_requires_raw_status_and_evidence() -> None:
    with pytest.raises(ValidationError, match="raw_status"):
        StatusExtraction(
            status=ApplicationStatus.FIRST_INTERVIEW,
            raw_status="",
            confidence=0.9,
            evidence="当前进度：一面",
        )

    unknown = StatusExtraction(
        status=ApplicationStatus.UNKNOWN,
        raw_status="",
        confidence=0.2,
        evidence="",
    )
    assert unknown.status is ApplicationStatus.UNKNOWN


def test_status_record_requires_timezone_aware_timestamps() -> None:
    aware = datetime(2026, 9, 25, 1, 2, tzinfo=UTC)
    record = StatusRecord(
        company="示例科技",
        role="AI 产品经理",
        url="https://careers.example.com/applications/123",
        status=ApplicationStatus.APPLIED,
        raw_status="申请已提交",
        confidence=0.95,
        evidence="我们已收到你的申请",
        checked_at=aware,
        changed_at=aware,
        check_result=CheckResult.SUCCESS,
    )

    assert record.checked_at == aware

    with pytest.raises(ValidationError, match="timezone-aware"):
        StatusRecord(
            **record.model_dump(exclude={"checked_at"}),
            checked_at=datetime(2026, 9, 25, 1, 2),
        )
