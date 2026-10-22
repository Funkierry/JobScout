"""Business regressions for paired job/resume evidence, using synthetic data."""

from collections import Counter

import pytest

from app.jobscout.matching import score_matches

RECORD = {"record_id": "role1", "公司": "Synthetic", "岗位": "Python 工程师", "任职要求": "熟悉 Python，参与 API 项目", "学历要求": "本科及以上"}
RESUMES = {"resume.md": "本科学历。熟悉 Python。开发 API 项目。"}


def evidence(**changes):
    return {"dimension": "skills", "points": 30, "requirement_field": "任职要求", "requirement_quote": "熟悉 Python", "matched_term": "Python", "resume_file": "resume.md", "resume_quote": "熟悉 Python", **changes}


def score(items, *, record=None, resumes=None):
    return score_matches([{"record_id": "role1", "score_items": items}], [record or RECORD], RESUMES if resumes is None else resumes)


@pytest.mark.parametrize("items", [[], [evidence(points=0)], [evidence(resume_quote="虚构经历")]])
def test_no_positive_verified_evidence_never_enters_recommendations(items):
    assert score(items) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"requirement_field": "missing"},
        {"requirement_quote": "熟悉 Java"},
        {"matched_term": "Java"},
        {"requirement_field": "公司", "requirement_quote": "Synthetic", "matched_term": "Synthetic"},
    ],
)
def test_job_side_and_corresponding_term_must_belong_to_the_same_record(changes):
    assert score([evidence(**changes)]) == []


def test_valid_pair_keeps_both_original_spans_and_does_not_trust_total():
    row = score([evidence()])[0]
    assert row["total"] == 30
    checked = next(item for item in row["score_items"] if item["dimension"] == "skills")
    assert checked["requirement_quote"] == "熟悉 Python"
    assert checked["resume_quote"] == "熟悉 Python"
    assert checked["matched_term"] == "Python"


def test_actual_upload_virtual_path_is_the_same_authorized_resume():
    rows = score([evidence(resume_file="/mnt/user-data/uploads/resume.md")])
    assert rows[0]["total"] == 30
    assert rows[0]["score_items"][1]["resume_file"] == "resume.md"


@pytest.mark.parametrize("filename", ["/tmp/resume.md", "/mnt/user-data/uploads/../resume.md", "/mnt/user-data/uploads/other/resume.md", "/mnt/user-data/uploads/unread.md"])
def test_virtual_path_alias_cannot_authorize_another_file(filename):
    assert score([evidence(resume_file=filename)]) == []


def test_one_resume_excerpt_cannot_fill_all_five_dimensions():
    from app.jobscout.matching import DIMENSIONS

    assert score([evidence(dimension=name, points=maximum) for name, maximum in DIMENSIONS.items()]) == []


def test_independent_skill_and_project_evidence_can_both_count():
    rows = score([evidence(), evidence(dimension="projects", points=20, requirement_quote="参与 API 项目", matched_term="API", resume_quote="开发 API 项目")])
    assert rows[0]["total"] == 50


def test_explicit_degree_conflict_excludes_even_a_valid_skill_pair():
    assert score([evidence()], record={**RECORD, "学历要求": "博士及以上"}) == []


def test_degree_preference_is_not_a_hard_exclusion():
    assert score([evidence()], record={**RECORD, "学历要求": "博士优先"})[0]["total"] == 30


@pytest.mark.parametrize("requirement", ["本科或硕士", "本科/硕士", "硕士以下"])
def test_degree_alternatives_and_upper_bounds_do_not_exclude_bachelor(requirement):
    assert score([evidence()], record={**RECORD, "学历要求": requirement})[0]["total"] == 30


def test_experience_range_uses_minimum_and_unknown_is_not_failure():
    record = {**RECORD, "工作经验": "3-5年工作经验"}
    assert score([evidence()], record=record, resumes={"resume.md": RESUMES["resume.md"] + "4年工作经验。"})[0]["total"] == 30
    assert score([evidence()], record=record)[0]["total"] == 30


def test_explicit_experience_conflict_excludes_candidate():
    assert score([evidence()], record={**RECORD, "工作经验": "5年以上工作经验"}, resumes={"resume.md": RESUMES["resume.md"] + "1年工作经验。"}) == []


def test_unrelated_excerpt_cannot_support_education_even_if_it_exists():
    assert score([evidence(dimension="education", points=10)]) == []


def test_scrum_master_is_not_a_masters_degree():
    assert (
        score(
            [evidence(dimension="education", points=10, requirement_field="学历要求", requirement_quote="Master's degree", matched_term="Master", resume_quote="Scrum Master")],
            record={**RECORD, "学历要求": "Master's degree"},
            resumes={"resume.md": "Scrum Master"},
        )
        == []
    )


def test_negated_skill_and_word_substrings_do_not_support_matching():
    assert score([evidence(resume_quote="不会 Python")], resumes={"resume.md": "本科学历。不会 Python。"}) == []
    assert score([evidence(requirement_quote="熟悉 Go", matched_term="Go", resume_quote="熟悉 Django")], record={**RECORD, "任职要求": "熟悉 Go"}, resumes={"resume.md": "本科学历。熟悉 Django。"}) == []


def test_missing_job_side_legacy_payload_fails_closed():
    legacy = evidence()
    for key in ("requirement_field", "requirement_quote", "matched_term"):
        legacy.pop(key)
    assert score([legacy]) == []


def test_malformed_dimension_cannot_crash_match_validation():
    assert score([evidence(dimension=[])]) == []


def test_low_proficiency_does_not_support_expert_requirement():
    assert score([evidence(requirement_quote="精通 Python", resume_quote="了解 Python")], record={**RECORD, "任职要求": "精通 Python"}, resumes={"resume.md": "本科学历。了解 Python。"}) == []


@pytest.mark.parametrize("quote", ["了解 Python", "Python"])
def test_direction_cannot_bypass_skill_proficiency_by_cropping_or_changing_field(quote):
    record = {**RECORD, "任职要求": "精通 Python"}
    item = evidence(dimension="direction", requirement_field="岗位", requirement_quote="Python 工程师", resume_quote=quote)
    assert score([item], record=record, resumes={"resume.md": "本科学历。了解 Python。"}) == []


def test_cropped_quote_cannot_hide_negation_in_uploaded_resume():
    assert score([evidence(resume_quote="Python")], resumes={"resume.md": "本科学历。不会 Python。"}) == []


def test_proficiency_of_other_skill_and_section_heading_do_not_zero_supported_skill():
    resume = "本科学历。数据与技术基础：熟练使用 Python，了解 Java。"
    item = evidence(requirement_quote="精通 Python", resume_quote="数据与技术基础：熟练使用 Python，了解 Java")
    assert score([item], record={**RECORD, "任职要求": "精通 Python"}, resumes={"resume.md": resume})[0]["total"] == 30


def test_cropped_repeated_term_cannot_choose_the_more_flattering_occurrence():
    resume = "本科学历。了解 Python。熟练使用 Python。"
    record = {**RECORD, "任职要求": "精通 Python"}
    assert score([evidence(requirement_quote="精通 Python", resume_quote="Python")], record=record, resumes={"resume.md": resume}) == []
    assert score([evidence(requirement_quote="精通 Python", resume_quote="熟练使用 Python")], record=record, resumes={"resume.md": resume})[0]["total"] == 30


def test_requirement_for_another_skill_does_not_raise_the_matching_threshold():
    record = {**RECORD, "任职要求": "熟练 Java，了解 Python"}
    assert score([evidence(requirement_quote="了解 Python", resume_quote="了解 Python")], record=record, resumes={"resume.md": "本科学历。了解 Python。"})[0]["total"] == 30


def test_rejection_audit_contains_counts_only():
    audit = Counter()
    assert score_matches([{"record_id": "role1", "score_items": []}], [RECORD], RESUMES, audit=audit) == []
    assert audit["no_positive_evidence"] == 1
    assert all(isinstance(value, int) for value in audit.values())
