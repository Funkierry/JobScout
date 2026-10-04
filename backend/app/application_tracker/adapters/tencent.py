"""Tencent currentStatus only; historical step numbers never determine status."""

from app.application_tracker.adapters.common import AdapterFallback, application_row, integer, object_value, role_text
from app.application_tracker.models import ApplicationStatus

HOSTS = frozenset({"join.qq.com"})
PATH = "/api/v1/apply/getApplyProcess"
NAME = "tencent"


def parse(body, *, role, checked_at):
    envelope = object_value(body)
    if integer(envelope.get("status")) != 0:
        raise AdapterFallback("api_error")
    data = object_value(envelope.get("data"))
    current = object_value(data.get("currentStatus"))
    if (integer(current.get("status")), integer(current.get("applyProcessType"))) != (2, 4):
        raise AdapterFallback("unknown_status")
    position = object_value(data.get("positionInfo"))
    title = role_text(position.get("applyPositionTxt"))
    if role != "待识别岗位" and role != title:
        raise AdapterFallback("role_not_found")
    if position.get("interviewPositionTxt") not in (None, "", title):
        raise AdapterFallback("ambiguous_role")
    # Quote only the status field; profile metadata must never enter the record.
    return [application_row(title, ApplicationStatus.RESUME_SCREENING, evidence='"status": 2')]
