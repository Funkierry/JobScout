from datetime import UTC, date, datetime

from app.application_tracker.dates import parse_grounded_applied_at

CHECKED_AT = datetime(2026, 9, 27, 4, 0, tzinfo=UTC)


def test_parses_chinese_application_date_from_page_evidence() -> None:
    parsed, evidence = parse_grounded_applied_at(
        "2026-09-15",
        "投递时间：2026年9月15日 10:30",
        page_text="岗位：产品经理\n投递时间：2026年9月15日 10:30",
        checked_at=CHECKED_AT,
    )

    assert parsed == date(2026, 9, 15)
    assert evidence == "投递时间：2026年9月15日 10:30"


def test_rejects_deadline_even_when_the_date_is_on_the_page() -> None:
    parsed, evidence = parse_grounded_applied_at(
        "2026-09-15",
        "投递截止日期：2026-09-15",
        page_text="投递截止日期：2026-09-15",
        checked_at=CHECKED_AT,
    )

    assert parsed is None
    assert evidence == ""


def test_rejects_date_not_supported_by_the_quoted_page_evidence() -> None:
    parsed, evidence = parse_grounded_applied_at(
        "2026-09-15",
        "投递时间：2026-09-14",
        page_text="投递时间：2026-09-14",
        checked_at=CHECKED_AT,
    )

    assert parsed is None
    assert evidence == ""


def test_rejects_unquoted_or_future_application_date() -> None:
    unquoted, _ = parse_grounded_applied_at(
        "2026-09-15",
        "投递时间：2026-09-15",
        page_text="页面没有投递时间",
        checked_at=CHECKED_AT,
    )
    future, _ = parse_grounded_applied_at(
        "2026-10-15",
        "投递时间：2026-10-15",
        page_text="投递时间：2026-10-15",
        checked_at=CHECKED_AT,
    )

    assert unquoted is None
    assert future is None


def test_does_not_confuse_job_publish_date_with_submission_date_on_the_same_line() -> None:
    parsed, evidence = parse_grounded_applied_at(
        "2026-09-01",
        "职位发布：2026-09-01；投递时间：2026-09-15",
        page_text="职位发布：2026-09-01；投递时间：2026-09-15",
        checked_at=CHECKED_AT,
    )

    assert parsed is None
    assert evidence == ""
