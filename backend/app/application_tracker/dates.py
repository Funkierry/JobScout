"""Validate application dates against verbatim recruitment-page evidence."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_ABSOLUTE_DATE = re.compile(r"(?<!\d)(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})(?:日)?(?!\d)")
_SUBMISSION_CONTEXT = re.compile(r"投递|申请|提交|报名|应聘|applied|submitted|application", re.IGNORECASE)
_DEADLINE_CONTEXT = re.compile(r"截止|截至|结束|到期|deadline|closing|close date", re.IGNORECASE)
_DATE_CONTEXT_LABEL = re.compile(r"投递|申请|提交|报名|应聘|applied|submitted|application|发布|刊登|publish|posted", re.IGNORECASE)


def _dates_in(text: str) -> set[date]:
    parsed: set[date] = set()
    for match in _ABSOLUTE_DATE.finditer(text):
        try:
            parsed.add(date(*(int(part) for part in match.groups())))
        except ValueError:
            continue
    return parsed


def _source_excerpt(page_text: str, evidence: str) -> str:
    candidate = evidence.strip()
    if not candidate:
        return ""
    if candidate in page_text:
        return candidate
    words = candidate.split()
    if not words:
        return ""
    match = re.search(r"\s+".join(re.escape(word) for word in words), page_text)
    return match.group(0) if match else ""


def _date_has_submission_context(evidence: str, candidate_date: date) -> bool:
    for match in _ABSOLUTE_DATE.finditer(evidence):
        try:
            matched_date = date(*(int(part) for part in match.groups()))
        except ValueError:
            continue
        if matched_date != candidate_date:
            continue
        prefix = evidence[max(0, match.start() - 40) : match.start()]
        labels = list(_DATE_CONTEXT_LABEL.finditer(prefix))
        if labels and _SUBMISSION_CONTEXT.fullmatch(labels[-1].group(0)):
            trailing = prefix[labels[-1].end() :]
            if not re.search(r"状态|进度|流程|发布|截止", trailing):
                return True
    return False


def parse_grounded_applied_at(
    proposed_date: str,
    proposed_evidence: str,
    *,
    page_text: str,
    checked_at: datetime,
) -> tuple[date | None, str]:
    """Accept only an absolute submission date copied from the current page."""
    if not proposed_date or not proposed_evidence:
        return None, ""
    candidate_dates = _dates_in(proposed_date.strip())
    if len(candidate_dates) != 1:
        return None, ""
    candidate_date = next(iter(candidate_dates))
    evidence = _source_excerpt(page_text, proposed_evidence)
    if not evidence or candidate_date not in _dates_in(evidence):
        return None, ""
    if _DEADLINE_CONTEXT.search(evidence) or not _date_has_submission_context(evidence, candidate_date):
        return None, ""
    # The page may be one calendar day ahead of a UTC check in a local timezone.
    if candidate_date > (checked_at + timedelta(days=1)).date():
        return None, ""
    return candidate_date, evidence
