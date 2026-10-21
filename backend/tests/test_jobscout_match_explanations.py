"""Requirement comparisons and recovery advice use synthetic, grounded data."""

from collections import Counter

import pytest

from app.jobscout.evidence import ResearchEvidence
from app.jobscout.match_requirements import qualification_checks
from app.jobscout.matching import score_matches
from app.jobscout.render import render_matches

RECORD = {"record_id": "r1", "公司": "Example", "岗位": "Python 工程师", "任职要求": "精通 Python", "学历要求": "本科及以上", "工作经验": "3-5年工作经验"}
RESUMES = {"cv.md": "本科学历。4年工作经验。了解 Python。"}


def candidate(**changes):
    return {
        "record_id": "r1",
        "score_items": [{"dimension": "skills", "points": 30, "requirement_field": "任职要求", "requirement_quote": "精通 Python", "matched_term": "Python", "resume_file": "cv.md", "resume_quote": "了解 Python", **changes}],
    }


def test_qualification_comparisons_preserve_both_sources_and_range_minimum():
    checks = qualification_checks(RECORD, RESUMES)
    assert len(checks) == 2 and {item["judgement"] for item in checks} == {"supported"}
    for item in checks:
        assert item["requirement_quote"] in RECORD[item["requirement_field"]]
        assert item["resume_quote"] in RESUMES[item["resume_file"]]
    assert checks[1]["requirement_quote"] == "3-5年工作经验"


@pytest.mark.parametrize("resume", ["开发 Python。", "预计获得博士学位。", "计划拥有5年工作经验。"])
def test_missing_or_future_qualifications_remain_unknown(resume):
    assert {item["judgement"] for item in qualification_checks(RECORD, {"cv.md": resume})} == {"unknown"}


@pytest.mark.parametrize("field,value", [("学历要求", "博士优先"), ("工作经验", "5年工作经验优先"), ("学历要求", "硕士以下")])
def test_preferences_and_upper_bounds_do_not_become_hard_requirements(field, value):
    checks = qualification_checks({"record_id": "r1", field: value}, RESUMES)
    assert checks == []


def test_limited_proficiency_keeps_grounded_evidence_without_scoring_or_recommending():
    excluded = []
    assert score_matches([candidate()], [RECORD], RESUMES, excluded=excluded) == []
    item = next(item for item in excluded[0]["score_items"] if item["dimension"] == "skills")
    assert item["judgement"] == "limited" and item["points"] == 0
    assert item["resume_quote"] == "了解 Python" and item["requirement_quote"] == "精通 Python"


def test_hard_conflict_has_reviewable_reason_but_audit_keeps_only_counts():
    excluded, audit = [], Counter()
    record = {**RECORD, "学历要求": "博士及以上", "任职要求": "了解 Python"}
    assert score_matches([candidate(requirement_quote="了解 Python")], [record], RESUMES, excluded=excluded, audit=audit) == []
    conflict = next(item for item in excluded[0]["requirement_checks"] if item["judgement"] == "not_met")
    assert conflict["requirement_quote"] == "博士及以上" and conflict["resume_quote"] == "本科学历"
    assert audit["education_conflict"] == 1 and all(type(v) is int for v in audit.values())


def test_invalid_quotes_cannot_reappear_as_explanations():
    excluded = []
    score_matches([candidate(resume_quote="虚构百万用户", requirement_quote="虚构岗位要求")], [RECORD], RESUMES, excluded=excluded)
    report = render_matches([], ResearchEvidence(records=[RECORD]), excluded=excluded)
    assert "虚构百万用户" not in report and "虚构岗位要求" not in report
    assert "待确认" in report and "补充" in report


def test_exclusion_details_are_bounded_and_cannot_show_foreign_or_duplicate_records():
    excluded = []
    records = [{**RECORD, "record_id": f"r{i}"} for i in range(20)]
    candidates = [{**candidate(), "record_id": f"r{i}"} for i in range(20)] + [candidate(), {**candidate(), "record_id": "foreign"}]
    score_matches(candidates, records, RESUMES, excluded=excluded)
    assert len(excluded) == 10 and len({row["record_id"] for row in excluded}) == 10
    assert "foreign" not in str(excluded)


def test_report_places_scores_in_details_and_explains_missing_requirements():
    record = {**RECORD, "任职要求": "了解 Python"}
    matches = score_matches([candidate(requirement_quote="了解 Python")], [record], RESUMES)
    report = render_matches(matches, ResearchEvidence(records=[record]))
    summary, detail = report.split("## 匹配依据", 1)
    assert "30/100" not in summary and "30/100" in detail
    assert "需要补充信息" in summary
    assert "有支持" in detail and "待确认" in detail and "cv\\.md" in detail
    assert "未逐条覆盖" in report


def test_limited_and_conflicting_requirements_render_distinct_judgements():
    excluded = []
    score_matches([candidate()], [RECORD], RESUMES, excluded=excluded)
    report = render_matches([], ResearchEvidence(records=[RECORD]), excluded=excluded)
    assert "支持有限" in report and "精通 Python" in report and "了解 Python" in report
    assert "没有通过双侧证据校验的推荐岗位" in report
    excluded.clear()
    record = {**RECORD, "学历要求": "博士及以上"}
    score_matches([candidate()], [record], RESUMES, excluded=excluded)
    assert "明确不满足" in render_matches([], ResearchEvidence(records=[record]), excluded=excluded)
