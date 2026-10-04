"""Xiaohongshu finished applications; no rejection/offer inferred from 'end'."""

from app.application_tracker.adapters.common import AdapterFallback, absolute_application_date, application_row, integer, object_value, role_text, rows_value, select_rows
from app.application_tracker.models import ApplicationStatus

HOSTS = frozenset({"job.xiaohongshu.com"})
PATH = "/websiterecruit/apply/record/list"
NAME = "xiaohongshu"


def parse(body, *, role, checked_at):
    envelope = object_value(body)
    if integer(envelope.get("statusCode")) != 200 or integer(envelope.get("errorCode")) != 200 or envelope.get("success") is not True:
        raise AdapterFallback("api_error")
    result = []
    for row in select_rows(rows_value(envelope.get("data")), role, lambda item: item.get("positionName")):
        if row.get("status") != "end" or row.get("statusGroup") != "finished":
            raise AdapterFallback("unknown_status")
        steps = rows_value(row.get("steps"))
        terminal = [step for step in steps if step.get("stepCode") == "terminal"]
        if len(terminal) != 1 or terminal[0].get("stepStatus") != "done":
            raise AdapterFallback("unknown_status")
        # More specific conclusions must be interpreted by the fallback, not discarded.
        description = str(row.get("statusDesc") or "").casefold()
        if any(word in description for word in ("offer", "录用", "未通过", "拒绝", "淘汰")):
            raise AdapterFallback("conflicting_status")
        applied_at, date_evidence = absolute_application_date(row.get("applyTime"), field="applyTime", checked_at=checked_at)
        result.append(application_row(role_text(row.get("positionName")), ApplicationStatus.TERMINATED, evidence='"status": "end"', applied_at=applied_at, applied_at_evidence=date_evidence))
    return result
