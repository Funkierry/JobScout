"""Conservative text checks for paired evidence, not a semantic truth oracle."""

import re

from app.evidence.grounding import ground_excerpt

FIELDS = {
    "direction": r"岗位|职位|职务|方向|title|role|position|job",
    "skills": r"技能|技术|专业|任职|要求|资格|职责|描述|jd|skill|require|qualification|description|dut|responsibilit",
    "projects": r"项目|经历|经验|任职|要求|资格|职责|描述|jd|project|experience|require|qualification|description|dut|responsibilit",
    "education": r"学历|学位|教育|任职|要求|资格|描述|jd|degree|education|require|qualification|description",
    "preferences": r"地点|城市|办公|工作方式|location|city|work.?mode|remote",
}
NON_REQUIREMENTS = re.compile(r"公司|企业|备注|链接|网址|company|employer|notes?|url|link|(?:^|_)id$", re.I)
GENERIC_TERMS = {
    "开发",
    "项目",
    "经验",
    "能力",
    "要求",
    "岗位",
    "职位",
    "工作",
    "技能",
    "学历",
    "熟悉",
    "熟练",
    "了解",
    "相关",
    "以上",
    "具备",
    "参与",
    "使用",
    "负责",
    "the",
    "and",
    "with",
    "work",
    "skill",
    "skills",
    "experience",
    "project",
    "development",
    "required",
}
DEGREES = [
    (1, r"大专|专科|\bassociate(?:['’]s\b| of\b|\s+degree\b)"),
    (2, r"本科|学士|\bbachelor(?:['’]s\b| of\b|\s+degree\b)"),
    (3, r"硕士|\bmaster(?:['’]s\b| of\b|\s+degree\b)"),
    (4, r"博士|\bdoctorate\b|\bph\.?d\b\.?"),
]
OPTIONAL = re.compile(r"不限|不要求|无需|优先|加分|可放宽|或同等|或相关|preferred|optional|or equivalent", re.I)
UPPER_BOUND = re.compile(r"以下|不高于|至多|不超过|at most|up to", re.I)
UNCERTAIN = re.compile(r"未取得|未获得|计划|希望|预计|在读|攻读|不具备|无.{0,6}(?:学历|学位)|pursuing|expected|aspir", re.I)


def degree(text):
    levels = [level for level, pattern in DEGREES if re.search(pattern, text, re.I)]
    return max(levels, default=0)


def resume_degree(text):
    # Future/negated degrees cannot establish an attained qualification.
    return max((degree(line) for line in re.split(r"[。；;\n]", text) if not UNCERTAIN.search(line)), default=0)


def minimum_degree(text):
    # Multiple degree alternatives cannot silently become the highest minimum.
    return min((level for level, pattern in DEGREES if re.search(pattern, text, re.I)), default=0)


def years(text):
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(?:年(?:以上)?(?:的)?(?:工作|相关|行业|开发)?经验|years?(?:\s+of)?\s+(?:work\s+)?experience)", text, re.I)
    return max((float(value) for value in matches), default=None)


def eligibility_issue(record, resumes):
    """Reject only explicit, directly comparable education/experience conflicts.

    Missing/ambiguous qualifications remain unknown and must be reviewed; age,
    gender and other sensitive traits are never inferred or used for ranking.
    """
    resume = "\n".join(resumes.values())
    actual_degree, actual_years = resume_degree(resume), years(resume)
    for field, value in record.items():
        if not isinstance(value, str) or NON_REQUIREMENTS.search(field):
            continue
        for clause in re.split(r"[。；;\n]", value[:20000]):
            if OPTIONAL.search(clause) or UPPER_BOUND.search(clause):
                continue
            education_field = re.search(r"学历|学位|education|degree", field, re.I)
            explicit_degree = re.search(r"(?:要求|必须|至少|最低).{0,8}(?:博士|硕士|本科|学士|专科)|(?:博士|硕士|本科|学士|专科)(?:及以上|以上)(?:学历|学位)?|requires?.{0,12}(?:doctorate|ph\.?d|master|bachelor)", clause, re.I)
            required_degree = minimum_degree(clause) if education_field or explicit_degree else 0
            if required_degree and actual_degree and actual_degree < required_degree:
                return "education_conflict"
            minimum_clause = re.sub(r"(\d+(?:\.\d+)?)\s*(?:-|–|—|~|～|至|到|to)\s*\d+(?:\.\d+)?", r"\1", clause, flags=re.I)
            required_years = years(minimum_clause) if re.search(r"经验|experience|要求|require", field, re.I) else None
            if required_years is not None and actual_years is not None and actual_years < required_years:
                return "experience_conflict"
    return ""


def contains_term(text, term):
    # Latin tokens must be words: Go must not match Django, or Java JavaScript.
    escaped = re.escape(term)
    pattern = (r"(?<![A-Za-z0-9_])" if term[0].isascii() and term[0].isalnum() else "") + escaped
    pattern += r"(?![A-Za-z0-9_])" if term[-1].isascii() and term[-1].isalnum() else ""
    return bool(re.search(pattern, text, re.I))


def paired_evidence(item, record, resume_span):
    field, quote, term = (item.get(name) for name in ("requirement_field", "requirement_quote", "matched_term"))
    dimension = item["dimension"]
    if not isinstance(field, str) or not re.search(FIELDS[dimension], field, re.I) or NON_REQUIREMENTS.search(field):
        return "invalid_requirement_field", {}
    value = record.get(field)
    if not isinstance(value, str) or not isinstance(quote, str) or not 0 < len(quote.strip()) <= 4000 or not (span := ground_excerpt(value, quote)):
        return "requirement_quote_not_found", {}
    if not isinstance(term, str) or not 2 <= len(term.strip()) <= 80 or term.strip().casefold() in GENERIC_TERMS or not any(char.isalpha() for char in term):
        return "invalid_corresponding_term", {}
    term = term.strip()
    if not contains_term(span, term) or not contains_term(resume_span, term):
        return "corresponding_term_not_found", {}
    # Negated or merely planned evidence is not a demonstrated match.
    if re.search(r"不(?:会|懂|熟悉|具备|了解)|尚未|未曾|没有|计划学习|希望学习|\b(?:no|not|without)\b", resume_span, re.I):
        return "unsupported_resume_claim", {}
    if dimension == "education" and (not minimum_degree(span) or not resume_degree(resume_span) or resume_degree(resume_span) < minimum_degree(span) or UPPER_BOUND.search(span)):
        return "unsupported_education", {}
    if dimension == "projects" and not re.search(r"项目|开发|搭建|设计|实现|负责|参与|建设|交付|project|built|develop|design|implement|deliver", resume_span, re.I):
        return "unsupported_project", {}
    if re.search(r"熟练|精通|proficient|expert", span, re.I) and re.search(r"了解|入门|基础|学习中|beginner|basic|learning", resume_span, re.I):
        return "proficiency_not_supported", {}
    return "", {"requirement_field": field, "requirement_quote": span, "matched_term": term}
