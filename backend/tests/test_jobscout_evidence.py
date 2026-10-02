"""Synthetic evidence only: no browser, credentials, or model invocation."""

from datetime import date

import pytest

from app.jobscout.evidence import ResearchEvidence, parse_payload
from app.jobscout.render import render_prep

URL = "https://example.test/a"


def candidate(**changes):
    return {"section": "business", "claim": "提供数据服务", "url": URL, "quote": "数据服务", **changes}


def research():
    evidence = ResearchEvidence(as_of=date(2026, 10, 3))
    evidence.observe({"name": "web_fetch", "args": {"url": URL}}, "2026-10-01 发布。提供数据\n服务。", "success")
    return evidence


def test_wrong_quote_and_cross_page_quote_are_discarded():
    evidence = research()
    evidence.observe({"name": "web_fetch", "args": {"url": "https://example.test/b"}}, "融资十亿元", "success")
    assert evidence.validate([candidate(quote="融资十亿元"), candidate(url="https://invented.test/a")]) == []
    assert evidence.rejected == {"quote_not_found": 1, "unseen_url": 1}


def test_whitespace_returns_source_span_and_snippet_is_labeled_by_code():
    evidence = research()
    valid = evidence.validate([candidate(quote="数据 服务")])
    assert valid[0]["quote"] == "数据\n服务"
    assert valid[0]["source_kind"] == "page"
    evidence.observe({"name": "web_search", "args": {}}, '[{"url":"https://example.test/s","snippet":"要求 Python"}]', "success")
    item = evidence.validate([candidate(url="https://example.test/s", quote="要求 Python", source_kind="page")])[0]
    assert item["source_kind"] == "search_snippet"
    assert "搜索摘要" in render_prep({}, [item], evidence)


@pytest.mark.parametrize("published,quote", [("2024-10-01", "2024-10-01"), ("2026-10-04", "2026-10-04"), ("2026-09-01", "2024-10-01"), (None, "2026-10-01")])
def test_old_future_missing_and_invented_publication_dates_fail(published, quote):
    evidence = research()
    evidence.observe({"name": "web_fetch", "args": {"url": URL}}, quote + " 数据服务", "success")
    assert evidence.validate([candidate(section="news", published_at=published, published_at_quote=quote)]) == []


def test_recent_date_requires_date_in_same_source_and_calendar_window_inclusive():
    evidence = research()
    assert len(evidence.validate([candidate(section="news", quote="数据 服务", published_at="2026-10-01", published_at_quote="2026-10-01 发布")])) == 1
    leap = ResearchEvidence(as_of=date(2024, 2, 29))
    leap.observe({"name": "web_fetch", "args": {"url": URL}}, "2023-02-28 数据服务", "success")
    assert len(leap.validate([candidate(section="news", published_at="2023-02-28", published_at_quote="2023-02-28")])) == 1


@pytest.mark.parametrize("quote", ["", "   ", None, 123])
def test_empty_or_wrong_type_quote_is_not_evidence(quote):
    assert research().validate([candidate(quote=quote)]) == []


def test_failed_tools_and_body_links_do_not_create_sources():
    evidence = ResearchEvidence()
    evidence.observe({"name": "web_fetch", "args": {"url": URL}}, "数据服务", "error")
    evidence.observe({"name": "web_search", "args": {}}, '[{"url":"https://example.test/b","snippet":"数据服务 https://example.test/a"}]', "success")
    assert evidence.validate([candidate()]) == []


def test_parser_and_renderer_never_fall_back_to_model_markdown():
    assert parse_payload("## 伪造章节\n任意断言", "jobscout_report") is None
    assert parse_payload('```jobscout_report\n{"company":"a","company":"b"}\n```', "jobscout_report") is None
    evidence = research()
    report = render_prep({"company": "坏\n## 伪造章节", "extra": "不应展示"}, [], evidence)
    assert "\n## 伪造章节" not in report and "不应展示" not in report
    assert "## 公司速览" in report and "## 证据边界与后续建议" in report
