"""Authenticated JobScout product endpoints.

This router deliberately exposes a narrow read-only operation instead of a
generic Lark CLI proxy.  The browser supplies a Base URL; the Gateway resolves
it with the current user's Feishu credentials and returns only bounded,
job-relevant fields for the matching workflow.
"""

import asyncio
import csv
import io
import json
import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.application_tracker.agent.workflow import ApplicationTrackerAgent
from app.application_tracker.csv_import import (
    DEFAULT_MAX_BYTES,
    CsvImportError,
    parse_application_csv,
)
from app.application_tracker.models import ApplicationInput
from app.application_tracker.store import (
    ApplicationTrackerStore,
    ImportSummary,
    StoredApplication,
    StoredCheck,
)
from app.application_tracker.update_service import (
    ApplicationNotFoundError,
    RefreshOutcome,
    TrackerUpdateService,
)
from app.gateway.authz import require_permission
from app.gateway.deps import get_config
from app.gateway.jobscout_base import JobScoutBaseError, load_job_base_context
from deerflow.config.app_config import AppConfig
from deerflow.integrations.lark_cli import get_lark_integration_status
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobscout", tags=["jobscout"])


def _tracker_store() -> ApplicationTrackerStore:
    return ApplicationTrackerStore()


def _new_tracker_agent() -> ApplicationTrackerAgent:
    return ApplicationTrackerAgent.from_model_name()


class TrackerRefreshResponse(BaseModel):
    application: StoredApplication
    skipped: bool
    reason: str | None

    @classmethod
    def from_outcome(cls, outcome: RefreshOutcome) -> "TrackerRefreshResponse":
        return cls(
            application=outcome.application,
            skipped=outcome.skipped,
            reason=outcome.reason,
        )


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


class TrackerApplicationCreate(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    role: str = Field(default="待识别岗位", max_length=300)
    applied_at: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return ApplicationInput.validate_http_url(value)


class TrackerApplicationPatch(BaseModel):
    company: str | None = Field(default=None, min_length=1, max_length=200)
    role: str | None = Field(default=None, min_length=1, max_length=300)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    applied_at: str | None = None
    notes: str | None = Field(default=None, max_length=5000)
    stage: str | None = Field(default=None, min_length=1, max_length=60)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return ApplicationInput.validate_http_url(value) if value else value


class TrackerStagesUpdate(BaseModel):
    stages: list[str] = Field(min_length=1, max_length=40)


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


@router.get(
    "/tracker/applications",
    response_model=list[StoredApplication],
    summary="List the current user's tracked applications",
)
@require_permission("runs", "read")
async def list_tracker_applications(request: Request) -> list[StoredApplication]:
    del request
    user_id = get_effective_user_id()
    return await asyncio.to_thread(_tracker_store().list_applications, user_id)


@router.post("/tracker/applications", response_model=StoredApplication)
@require_permission("runs", "create")
async def create_tracker_application(body: TrackerApplicationCreate, request: Request) -> StoredApplication:
    del request
    try:
        application = ApplicationInput(company=body.company, role=body.role or "待识别岗位", url=body.url, applied_at=body.applied_at)
        return await asyncio.to_thread(_tracker_store().add_application, get_effective_user_id(), application)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/tracker/applications/{application_id}", response_model=StoredApplication)
@require_permission("runs", "create")
async def patch_tracker_application(application_id: int, body: TrackerApplicationPatch, request: Request) -> StoredApplication:
    del request
    try:
        row = await asyncio.to_thread(_tracker_store().update_application, get_effective_user_id(), application_id, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Application was not found")
    return row


@router.delete("/tracker/applications/{application_id}", status_code=204)
@require_permission("runs", "create")
async def delete_tracker_application(application_id: int, request: Request) -> Response:
    del request
    deleted = await asyncio.to_thread(_tracker_store().delete_application, get_effective_user_id(), application_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Application was not found")
    return Response(status_code=204)


@router.get("/tracker/stages", response_model=list[str])
@require_permission("runs", "read")
async def get_tracker_stages(request: Request) -> list[str]:
    del request
    return await asyncio.to_thread(_tracker_store().list_stages, get_effective_user_id())


@router.put("/tracker/stages", response_model=list[str])
@require_permission("runs", "create")
async def put_tracker_stages(body: TrackerStagesUpdate, request: Request) -> list[str]:
    del request
    try:
        return await asyncio.to_thread(_tracker_store().replace_stages, get_effective_user_id(), body.stages)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/tracker/export.csv")
@require_permission("runs", "read")
async def export_tracker_csv(request: Request) -> Response:
    del request
    rows = await asyncio.to_thread(_tracker_store().list_applications, get_effective_user_id())
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["公司", "岗位", "投递日期", "自定义环节", "识别状态", "页面原始状态", "查询链接", "识别结果", "最近检查时间", "置信度", "证据"])
    for row in rows:
        writer.writerow(
            [
                row.company,
                row.role,
                row.applied_at or "",
                row.stage,
                row.status.value,
                row.raw_status,
                row.url,
                row.check_result.value if row.check_result else "",
                row.checked_at.isoformat() if row.checked_at else "",
                row.confidence,
                row.evidence,
            ]
        )
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=jobscout-applications.csv"})


@router.post(
    "/tracker/import",
    response_model=ImportSummary,
    summary="Import or update tracked applications from CSV",
)
@require_permission("runs", "create")
async def import_tracker_csv(
    request: Request,
    file: UploadFile = File(...),
) -> ImportSummary:
    del request
    data = await file.read(DEFAULT_MAX_BYTES + 1)
    try:
        applications = parse_application_csv(data)
    except CsvImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await asyncio.to_thread(
        _tracker_store().import_applications,
        get_effective_user_id(),
        applications,
    )


@router.get(
    "/tracker/applications/{application_id}/history",
    response_model=list[StoredCheck],
    summary="List one tracked application's check history",
)
@require_permission("runs", "read")
async def list_tracker_application_history(
    application_id: int,
    request: Request,
) -> list[StoredCheck]:
    del request
    user_id = get_effective_user_id()
    store = _tracker_store()
    application = await asyncio.to_thread(store.get_application, user_id, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application was not found")
    return await asyncio.to_thread(store.list_checks, user_id, application_id)


@router.post(
    "/tracker/applications/{application_id}/refresh",
    response_model=TrackerRefreshResponse,
    summary="Refresh one non-terminal tracked application",
)
@require_permission("runs", "create")
async def refresh_tracker_application(
    application_id: int,
    request: Request,
) -> TrackerRefreshResponse:
    del request
    agent = _new_tracker_agent()
    try:
        service = TrackerUpdateService(store=_tracker_store(), agent=agent)
        try:
            outcome = await service.refresh_one(get_effective_user_id(), application_id)
        except ApplicationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Application was not found") from exc
        return TrackerRefreshResponse.from_outcome(outcome)
    finally:
        await agent.aclose()


@router.post(
    "/tracker/refresh-all",
    summary="Sequentially refresh all non-terminal applications",
)
@require_permission("runs", "create")
async def refresh_all_tracker_applications(request: Request) -> StreamingResponse:
    del request
    user_id = get_effective_user_id()
    store = _tracker_store()

    async def event_stream():
        agent = _new_tracker_agent()
        try:
            service = TrackerUpdateService(store=store, agent=agent)
            async for event in service.stream_refresh_all(user_id):
                payload = jsonable_encoder(asdict(event))
                yield f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
        finally:
            await agent.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
