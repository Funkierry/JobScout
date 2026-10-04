"""Observed Feishu/ByteDance application schema, restricted to verified hosts."""

import json

from app.application_tracker.adapters.common import AdapterFallback, application_row, integer, object_value, role_text, rows_value, select_rows
from app.application_tracker.models import ApplicationStatus

HOSTS = frozenset(
    {"jobs.bytedance.com", "xiaomi.jobs.f.mioffice.cn", "arashivision.jobs.feishu.cn", "campus.dewu.com", "wepie.jobs.feishu.cn", "xiaopeng.jobs.feishu.cn", "nio.jobs.feishu.cn", "campus.duxiaoman.com", "hf7l9aiqzx.jobs.feishu.cn"}
)
PATH = "/api/v1/search/user/applications"
NAME = "feishu_bytedance"


def parse(body, *, role, checked_at):
    envelope = object_value(body)
    if integer(envelope.get("code")) != 0:
        raise AdapterFallback("api_error")
    data = object_value(envelope.get("data"))
    rows = rows_value(data.get("delivery_list"))

    def title(row):
        return object_value(row.get("job_post_info")).get("title")

    result = []
    for row in select_rows(rows, role, title):
        stage = object_value(row.get("current_stage"))
        # Extra status/name fields represent schema drift; never ignore a conflict.
        if set(stage) != {"stage_id"}:
            raise AdapterFallback("schema_mismatch")
        code = integer(stage.get("stage_id"))
        mapped = {0: ApplicationStatus.APPLIED, 1: ApplicationStatus.TERMINATED}.get(code)
        if mapped is None:
            raise AdapterFallback("unknown_status")
        result.append(application_row(role_text(title(row)), mapped, evidence=json.dumps(stage, ensure_ascii=False), raw_status=f'"stage_id": {code}'))
    return result
