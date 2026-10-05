"""Bounded full-row pages with compact global status projections.

Counts scan small status fields, never every application's evidence/notes. Mail
resolution stays in MailStore so pagination cannot disagree with single reads.
"""

from collections import Counter
from datetime import datetime

from pydantic import BaseModel

from .email.store import TERMINAL, MailStore
from .models import ApplicationStatus, CheckResult


class SummaryRow(BaseModel):
    id: int
    status: ApplicationStatus
    stage: str
    stage_manual: bool
    confidence: float
    checked_at: datetime | None
    check_result: CheckResult | None
    source_summary: dict | None = None


def presentation(row):
    source = row.source_summary or {}
    mail = source.get("source") == "email" and not source.get("conflict")
    status = source["status"] if mail else row.status.value
    stage = source["status"] if mail and not row.stage_manual else row.stage or status
    review = bool(source.get("conflict") or status == "未知" or (not mail and ((row.checked_at and row.confidence < 0.7) or (row.check_result and row.check_result is not CheckResult.SUCCESS))))
    if review:
        bucket = "review"
    elif status == "Offer":
        bucket = "offers"
    elif status not in TERMINAL:
        bucket = "active"
    else:
        bucket = None
    return stage, bucket


def list_page(store, user_id, *, limit=50, before_id=None, stage=None):
    user_id = store._validated_user_id(user_id)
    if not 1 <= limit <= 100 or (before_id is not None and before_id < 1):
        raise ValueError("Invalid page bounds")
    with store._connect() as connection:
        connection.execute("BEGIN")
        rows = connection.execute("SELECT id,status,stage,stage_manual,confidence,checked_at,check_result FROM applications WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
        projected = MailStore._decorate([SummaryRow.model_validate(dict(row)) for row in rows], MailStore._events(connection, user_id, compact=True))
        summary = {"total": len(projected), "active": 0, "review": 0, "offers": 0}
        stages, matching = Counter(), []
        for row in projected:
            label, bucket = presentation(row)
            stages[label] += 1
            if bucket:
                summary[bucket] += 1
            if stage is None or label == stage:
                matching.append(row.id)
        eligible = [row_id for row_id in matching if before_id is None or row_id < before_id]
        ids = eligible[:limit]
        items = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            selected = connection.execute(f"SELECT * FROM applications WHERE user_id=? AND id IN ({placeholders}) ORDER BY id DESC LIMIT ?", (user_id, *ids, limit)).fetchall()
            items = MailStore._decorate([store._application_from_row(row) for row in selected], MailStore._events(connection, user_id, application_ids=ids))
    has_more = len(eligible) > limit
    return {
        "items": items,
        "total": summary["total"],
        "filtered_total": len(matching),
        "summary": summary,
        "stages": [{"label": label, "count": count} for label, count in stages.items()],
        "has_more": has_more,
        "next_before_id": ids[-1] if has_more else None,
    }
