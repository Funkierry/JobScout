"""Synthetic provenance and Markdown regression cases; no network or model."""

import pytest

from app.jobscout.links import collect_seen_urls, normalize_url, strip_unseen_links


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Example.CN:443/a?q=1#part", "https://example.cn/a?q=1"),
        ("http://Example.CN:80", "http://example.cn/"),
        ("https://example.cn:8443/a", "https://example.cn:8443/a"),
        ("https://example.cn/a?x=1&y=2", "https://example.cn/a?x=1&y=2"),
        ("javascript:alert(1)", None),
        ("//example.cn/a", None),
        ("https://user:password@example.cn/a", None),
        ("https://example.cn:bad", None),
    ],
)
def test_url_normalization(raw, expected):
    assert normalize_url(raw) == expected


def test_only_actual_successful_tool_results_are_sources():
    messages = [
        {"type": "tool", "name": "web_search", "content": '[{"url":"https://example.cn/a", "snippet":"https://invented.cn/"}]'},
        {"type": "tool", "name": "task", "content": '[{"url":"https://invented.cn/task"}]'},
        {"type": "human", "name": "web_search", "content": '[{"url":"https://invented.cn/user"}]'},
        {"type": "tool", "name": "web_search", "status": "error", "content": '[{"url":"https://invented.cn/error"}]'},
        {"type": "ai", "tool_calls": [{"id": "f1", "name": "web_fetch", "args": {"url": "https://example.cn/fetched"}}]},
        {"type": "tool", "name": "web_fetch", "tool_call_id": "f1", "content": "# A real fetched document"},
        {"type": "ai", "tool_calls": [{"id": "f2", "name": "web_fetch", "args": {"url": "https://invented.cn/failed"}}]},
        {"type": "tool", "name": "web_fetch", "tool_call_id": "f2", "content": "Error: forbidden"},
        {"type": "ai", "tool_calls": [{"id": "f3", "name": "web_fetch", "args": {"url": "https://invented.cn/unexecuted"}}]},
    ]
    assert collect_seen_urls(messages) == {"https://example.cn/a", "https://example.cn/fetched"}


@pytest.mark.parametrize(
    "report",
    [
        "[伪造](https://invented.cn/a)",
        "[伪造][ref]\n\n[ref]: https://invented.cn/a",
        "[ref][]\n\n[ref]: <https://invented.cn/a> 'title'",
        "[ref]\n\n[ref]: https://invented.cn/a",
        "<https://invented.cn/a>",
        "https://invented.cn/a",
        '<a href="https://invented.cn/a">伪造</a>',
        '<img src="https://invented.cn/a">',
        '[伪造](https://invented.cn/a(b)c "title")',
        "[伪造](javascript:alert(1))",
        "[伪造](//invented.cn/a)",
        "[伪造](/a)",
        "[伪造](https&#58;//invented.cn/a)",
    ],
)
def test_no_link_syntax_can_bypass_an_empty_allowlist(report):
    cleaned, count = strip_unseen_links(report, set())
    assert "invented.cn" not in cleaned
    assert "（来源未核验）" in cleaned
    assert count >= 1
    assert strip_unseen_links(cleaned, set()) == (cleaned, 0)


def test_preserves_allowed_links_and_surrounding_markdown_without_domain_wildcards():
    report = "## 公司\n**事实** [官网](https://EXAMPLE.cn:443/a#section)\n| 题目 | [猜测](https://example.cn/b) |"
    cleaned, count = strip_unseen_links(report, {"https://example.cn/a"})
    assert cleaned == "## 公司\n**事实** [官网](https://example.cn/a)\n| 题目 | 猜测（来源未核验） |"
    assert count == 1


def test_reference_links_are_resolved_and_observed_urls_keep_business_queries():
    report = "[来源][a] 和 [猜测](https://example.cn/a?job=2)\n\n[a]: https://example.cn/a?job=1"
    cleaned, count = strip_unseen_links(report, {"https://example.cn/a?job=1"})
    assert "[来源](https://example.cn/a?job=1)" in cleaned
    assert "job=2" not in cleaned
    assert count == 1


def test_fetch_requires_a_matched_call_and_nonempty_nonerror_result():
    assert collect_seen_urls([{"type": "tool", "name": "web_fetch", "content": "https://invented.cn"}]) == set()
    for body in ("", "No results found", "Error fetching content: unavailable", '{"error":"forbidden"}'):
        assert (
            collect_seen_urls(
                [
                    {"type": "ai", "tool_calls": [{"id": "f", "name": "web_fetch", "args": {"url": "https://example.cn"}}]},
                    {"type": "tool", "name": "web_fetch", "tool_call_id": "f", "content": body},
                ]
            )
            == set()
        )


def test_malformed_bracket_heavy_markdown_and_size_limit():
    from app.jobscout.links import MAX_REPORT_CHARS

    report = "[" * 100_000 + "https://invented.cn/a"
    cleaned, count = strip_unseen_links(report, set())
    assert "https://" not in cleaned and count == 1
    with pytest.raises(ValueError, match="validation limit"):
        strip_unseen_links("a" * (MAX_REPORT_CHARS + 1), set())
