"""Strict schema helpers; all failures use fixed, non-private reason codes."""

from __future__ import annotations

import json
import re
from datetime import date, datetime

from app.application_tracker.models import ApplicationStatus, DiscoveredApplication


class AdapterFallback(ValueError):
    pass


def object_value(value) -> dict:
    if not isinstance(value, dict):
        raise AdapterFallback("schema_mismatch")
    return value


def rows_value(value) -> list:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise AdapterFallback("empty_or_oversized_listing")
    if not all(isinstance(row, dict) for row in value):
        raise AdapterFallback("schema_mismatch")
    return value


def integer(value) -> int:
    if type(value) is not int:
        raise AdapterFallback("schema_mismatch")
    return value


def role_text(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 300 or "[REDACTED]" in value:
        raise AdapterFallback("invalid_role")
    return value.strip()


def select_rows(rows: list[dict], role: str, get_role) -> list[dict]:
    names = [role_text(get_role(row)) for row in rows]
    if len(names) != len(set(names)):
        raise AdapterFallback("ambiguous_role")
    if role == "待识别岗位":
        return rows
    selected = [row for row, name in zip(rows, names, strict=True) if name == role]
    if not selected:
        raise AdapterFallback("role_not_found")
    return selected


def application_row(role: str, status: ApplicationStatus, *, evidence: str, raw_status: str | None = None, applied_at: str = "", applied_at_evidence: str = "") -> DiscoveredApplication:
    # 1.0 is a deterministic rule score, not a calibrated probability.
    return DiscoveredApplication(role=role, role_evidence=role, status=status, raw_status=raw_status or evidence, confidence=1.0, evidence=evidence, applied_at=applied_at, applied_at_evidence=applied_at_evidence)


def absolute_application_date(value, *, field: str, checked_at: datetime) -> tuple[str, str]:
    # Only the schema's explicit application timestamp; no epoch units or timezone guesses.
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?", value):
        return "", ""
    try:
        parsed = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value).date()
    except ValueError:
        return "", ""
    if parsed > checked_at.date():
        return "", ""
    return parsed.isoformat(), f"{json.dumps(field)}: {json.dumps(value, ensure_ascii=False)}"
