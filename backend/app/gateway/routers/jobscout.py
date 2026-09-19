"""Authenticated JobScout product endpoints.

This router deliberately exposes a narrow read-only operation instead of a
generic Lark CLI proxy.  The browser supplies a Base URL; the Gateway resolves
it with the current user's Feishu credentials and returns only bounded,
job-relevant fields for the matching workflow.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.deps import get_config
from app.gateway.jobscout_base import JobScoutBaseError, load_job_base_context
from deerflow.config.app_config import AppConfig
from deerflow.integrations.lark_cli import get_lark_integration_status
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobscout", tags=["jobscout"])


class JobBaseContextRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048, description="Feishu/Lark Base or Base wiki URL")
    table_id: str | None = Field(default=None, min_length=5, max_length=131, description="Optional table id override")
    limit: int = Field(default=200, ge=1, le=200, description="Maximum number of records to read")


class JobBaseContextResponse(BaseModel):
    table_id: str
    table_name: str
    view_id: str | None
    fields: list[str]
    records: list[dict[str, object]]
    record_count: int
    has_more: bool
    context_truncated: bool


@router.post(
    "/base-context",
    response_model=JobBaseContextResponse,
    summary="Load bounded job records from a Feishu Base",
)
@require_permission("runs", "create")
async def load_base_context(
    body: JobBaseContextRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
) -> JobBaseContextResponse:
    del request  # Required by the auth decorator.
    user_id = get_effective_user_id()

    try:
        status = await asyncio.to_thread(
            get_lark_integration_status,
            user_id,
            config,
            check_latest=False,
            check_runtime=True,
        )
    except Exception as exc:
        logger.exception("Failed to inspect Lark integration for JobScout")
        raise HTTPException(status_code=503, detail="暂时无法检查飞书连接状态，请稍后重试。") from exc

    if not status.installed or not status.app_configured:
        raise HTTPException(status_code=409, detail="请先在 Capability Center 完成飞书应用配置。")
    if status.auth.status != "authenticated":
        raise HTTPException(status_code=409, detail="飞书授权未生效或已过期，请重新连接后再试。")
    if not status.cli.available:
        raise HTTPException(status_code=503, detail="Gateway 尚未安装可用的 Lark CLI。")

    try:
        context = await asyncio.to_thread(
            load_job_base_context,
            user_id=user_id,
            url=body.url,
            limit=body.limit,
            table_id=body.table_id,
        )
    except JobScoutBaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected failure while loading a JobScout Base context")
        raise HTTPException(status_code=503, detail="读取飞书岗位表失败，请稍后重试。") from exc

    return JobBaseContextResponse(
        table_id=context.table_id,
        table_name=context.table_name,
        view_id=context.view_id,
        fields=context.fields,
        records=context.records,
        record_count=context.record_count,
        has_more=context.has_more,
        context_truncated=context.context_truncated,
    )
