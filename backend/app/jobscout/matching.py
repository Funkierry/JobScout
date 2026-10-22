"""Score only known records and exact resume evidence; never trust totals."""

import re
from collections import Counter

from app.evidence.grounding import ground_excerpt

from .match_requirements import paired_evidence, qualification_checks

DIMENSIONS = {"direction": 30, "skills": 30, "projects": 20, "education": 10, "preferences": 10}
DIMENSION_LABELS = {"direction": "岗位方向", "skills": "技能", "projects": "项目", "education": "学历", "preferences": "地点与偏好"}


def score_matches(candidates, records, resumes, *, audit=None, excluded=None):
    audit = audit if audit is not None else Counter()
    known = {row["record_id"]: row for row in records if isinstance(row, dict) and isinstance(row.get("record_id"), str)}
    if not isinstance(candidates, list) or len(candidates) > 200:
        return []
    result, visited = [], set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        record_id = candidate.get("record_id")
        if not isinstance(record_id, str) or record_id not in known or record_id in visited:
            continue
        visited.add(record_id)
        requirements = qualification_checks(known[record_id], resumes)
        if issue := next((item["reason"] for item in requirements if item["judgement"] == "not_met"), ""):
            audit[issue] += 1
            if excluded is not None and len(excluded) < 10:
                excluded.append({"record_id": record_id, "record": known[record_id], "requirement_checks": requirements, "score_items": [], "reason": issue, "total": 0})
            continue
        items = candidate.get("score_items")
        items = items if isinstance(items, list) and len(items) <= 20 else []
        counts = Counter(item.get("dimension") for item in items if isinstance(item, dict) and isinstance(item.get("dimension"), str))
        # Whitespace variants cannot multiply one excerpt across dimensions.
        excerpts = Counter(
            re.sub(r"\s+", "", item["resume_quote"]).casefold() for item in items if isinstance(item, dict) and isinstance(item.get("dimension"), str) and item["dimension"] in DIMENSIONS and isinstance(item.get("resume_quote"), str)
        )
        checked = []
        for dimension, maximum in DIMENSIONS.items():
            item = next((item for item in items if isinstance(item, dict) and item.get("dimension") == dimension), {})
            reason, points, span = "", item.get("points"), None
            filename, quote = item.get("resume_file"), item.get("resume_quote")
            # Models sometimes echo the exact read_file virtual path. Resolve
            # only that prefix against already-read uploads, never a basename
            # from an arbitrary path or a new filesystem read.
            if isinstance(filename, str) and filename.startswith("/mnt/user-data/uploads/"):
                filename = filename.removeprefix("/mnt/user-data/uploads/")
            pair = {}
            if counts[dimension] != 1:
                reason = "missing_or_duplicate_dimension"
            elif type(points) is not int or not 0 <= points <= maximum:
                reason = "invalid_points"
            elif not isinstance(filename, str) or filename not in resumes or not isinstance(quote, str) or not 0 < len(quote.strip()) <= 4000 or not (span := ground_excerpt(resumes[filename], quote)):
                reason = "resume_quote_not_found"
            elif excerpts[re.sub(r"\s+", "", quote).casefold()] != 1:
                reason = "reused_resume_excerpt"
            else:
                reason, pair = paired_evidence(item, known[record_id], span, resume_text=resumes[filename])
            if reason:
                audit[reason] += 1
                audit["zeroed_items"] += 1
            limited = reason == "proficiency_not_supported"
            checked.append(
                {
                    "dimension": dimension,
                    "points": 0 if reason else points,
                    "resume_file": filename if not reason or limited else "",
                    "resume_quote": span if not reason or limited else "",
                    "reason": reason,
                    "judgement": "limited" if limited else "supported" if not reason and points > 0 else "unknown",
                    **pair,
                }
            )
        total = sum(item["points"] for item in checked)
        if total <= 0:
            audit["no_positive_evidence"] += 1
            if excluded is not None and len(excluded) < 10:
                excluded.append({"record_id": record_id, "record": known[record_id], "requirement_checks": requirements, "score_items": checked, "reason": "no_positive_evidence", "total": 0})
            continue
        result.append({"record_id": record_id, "record": known[record_id], "score_items": checked, "requirement_checks": requirements, "total": total})
    return sorted(result, key=lambda row: (-row["total"], row["record_id"]))[:10]
