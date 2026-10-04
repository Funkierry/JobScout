"""Deterministic, read-only ATS dispatch before any model initialization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from pydantic import ValidationError

from app.application_tracker.adapters import feishu, tencent, xiaohongshu
from app.application_tracker.adapters.common import AdapterFallback
from app.application_tracker.models import ApplicationInput, CheckResult, StatusRecord
from app.application_tracker.observations import CapturedJsonResponse, same_host
from app.evidence.grounding import is_evidence_grounded

ADAPTERS = (feishu, tencent, xiaohongshu)


@dataclass(frozen=True, slots=True)
class AdapterDecision:
    adapter: str | None = None
    record: StatusRecord | None = None
    reason: str | None = None


def try_adapters(application: ApplicationInput, responses: list[CapturedJsonResponse] | tuple[CapturedJsonResponse, ...], *, previous: StatusRecord | None = None, checked_at: datetime | None = None) -> AdapterDecision:
    checked_at = checked_at or datetime.now(UTC)
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("checked_at must be timezone-aware")
    host = (urlsplit(application.url).hostname or "").lower().rstrip(".")
    adapter = next((item for item in ADAPTERS if host in item.HOSTS), None)
    if adapter is None:
        return AdapterDecision(reason="unsupported_host")
    matches = [item for item in responses[:20] if same_host(item.url, application.url) and urlsplit(item.url).scheme == "https" and urlsplit(item.url).port in (None, 443) and urlsplit(item.url).path == adapter.PATH]
    if not matches:
        return AdapterDecision(adapter=adapter.NAME, reason="no_matching_response")
    response = matches[-1]
    if response.truncated or not 200 <= response.status < 300 or response.body is None:
        return AdapterDecision(adapter=adapter.NAME, reason="unusable_response")
    try:
        rows = adapter.parse(response.body, role=application.role, checked_at=checked_at)
        source = response.evidence_text()
        if any(not is_evidence_grounded(source, row.evidence) or not is_evidence_grounded(source, row.role_evidence) or (row.applied_at_evidence and not is_evidence_grounded(source, row.applied_at_evidence)) for row in rows):
            raise AdapterFallback("ungrounded_evidence")
        first = rows[0]
        record = StatusRecord(
            company=application.company,
            role=application.role,
            url=application.url,
            status=first.status,
            raw_status=first.raw_status,
            confidence=first.confidence,
            evidence=first.evidence,
            applied_at=date.fromisoformat(first.applied_at) if first.applied_at else None,
            applied_at_evidence=first.applied_at_evidence,
            detected_role=first.role if application.role == "待识别岗位" and len(rows) == 1 else "",
            discovered_applications=rows,
            checked_at=checked_at,
            changed_at=previous.changed_at if previous and previous.status is first.status else checked_at,
            check_result=CheckResult.SUCCESS,
        )
    except AdapterFallback as exc:
        return AdapterDecision(adapter=adapter.NAME, reason=str(exc))
    except (ValidationError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        return AdapterDecision(adapter=adapter.NAME, reason="schema_mismatch")
    return AdapterDecision(adapter=adapter.NAME, record=record)
