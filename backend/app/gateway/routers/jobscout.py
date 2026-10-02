"""Authenticated JobScout Base, opportunity, and application endpoints."""

import asyncio
import csv
import io
import json
import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

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
    StoredMatchCandidate,
    StoredOpportunity,
)
from app.application_tracker.update_service import (
    ApplicationNotFoundError,
    RefreshOutcome,
    TrackerUpdateService,
)
from app.gateway.authz import require_permission
from app.gateway.deps import get_config, get_thread_store
from app.gateway.jobscout_base import JobScoutBaseError, load_job_base_context, validate_base_url
from app.jobscout.inputs import BASE_SNAPSHOTS
from deerflow.config.app_config import AppConfig
from deerflow.integrations.lark_cli import get_lark_integration_status
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.utils.thread_id import ThreadId

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
    thread_id: ThreadId | None = None
    url: str = Field(..., min_length=1, max_length=2048, description="Feishu/Lark Base or Base wiki URL")
    table_id: str | None = Field(default=None, min_length=5, max_length=131, description="Optional table id override")
    limit: int = Field(default=200, ge=1, le=200, description="Maximum number of records to read")


class JobBaseContextResponse(BaseModel):
    context_ref: str | None = None
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


class OpportunityCreate(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=300)
    recruitment_type: str = Field(default="", max_length=20)
    source_kind: str = Field(default="manual", pattern="^(manual|feishu|tracker)$")
    source_url: str = Field(default="", max_length=2048)
    source_table_id: str = Field(default="", max_length=131)
    source_record_id: str = Field(default="", max_length=200)
    jd_text: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def validate_source(self) -> "OpportunityCreate":
        if self.source_kind == "feishu":
            if not self.source_url or not self.source_table_id or not self.source_record_id:
                raise ValueError("Feishu source needs URL, table ID and record ID")
            try:
                self.source_url = validate_base_url(self.source_url)
            except JobScoutBaseError as exc:
                raise ValueError(str(exc)) from exc
        elif self.source_url or self.source_table_id or self.source_record_id:
            raise ValueError("Source identifiers are only allowed for Feishu opportunities")
        return self


class OpportunityThreadLink(BaseModel):
    thread_id: ThreadId
    mode: str = Field(pattern="^(prep|match)$")


class OpportunityApplicationLink(BaseModel):
    application_id: int = Field(gt=0)


class MatchCandidateWrite(BaseModel):
    record_id: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=300)
    jd_text: str = Field(default="", max_length=8000)


class MatchCandidatesWrite(BaseModel):
    source_url: str = Field(max_length=2048)
    source_table_id: str = Field(min_length=1, max_length=131)
    candidates: list[MatchCandidateWrite] = Field(max_length=10)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        try:
            return validate_base_url(value)
        except JobScoutBaseError as exc:
            raise ValueError(str(exc)) from exc


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
    user_id = get_effective_user_id()
    if body.thread_id and await get_thread_store(request).get(body.thread_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Thread was not found")

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
        context_ref=BASE_SNAPSHOTS.put(user_id, body.thread_id, context.records, has_more=context.has_more, context_truncated=context.context_truncated) if body.thread_id else None,
        table_id=context.table_id,
        table_name=context.table_name,
        view_id=context.view_id,
        fields=context.fields,
        records=context.records,
        record_count=context.record_count,
        has_more=context.has_more,
        context_truncated=context.context_truncated,
    )


@router.get("/opportunities", response_model=list[StoredOpportunity])
@require_permission("runs", "read")
async def list_opportunities(request: Request) -> list[StoredOpportunity]:
    del request
    return await asyncio.to_thread(_tracker_store().list_opportunities, get_effective_user_id())


@router.post("/opportunities", response_model=StoredOpportunity)
@require_permission("runs", "create")
async def create_opportunity(body: OpportunityCreate, request: Request) -> StoredOpportunity:
    del request
    try:
        return await asyncio.to_thread(_tracker_store().create_opportunity, get_effective_user_id(), **body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/opportunities/{opportunity_id}", status_code=204)
@require_permission("runs", "create")
async def delete_opportunity(opportunity_id: int, request: Request) -> Response:
    del request
    deleted = await asyncio.to_thread(_tracker_store().delete_opportunity, get_effective_user_id(), opportunity_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Opportunity was not found")
    return Response(status_code=204)


@router.post("/opportunities/{opportunity_id}/threads", response_model=StoredOpportunity)
@require_permission("runs", "create")
async def link_opportunity_thread(opportunity_id: int, body: OpportunityThreadLink, request: Request) -> StoredOpportunity:
    user_id = get_effective_user_id()
    if await get_thread_store(request).get(body.thread_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Thread was not found")
    store = _tracker_store()
    try:
        linked = await asyncio.to_thread(store.link_opportunity_thread, user_id, opportunity_id, body.thread_id, body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not linked:
        raise HTTPException(status_code=404, detail="Opportunity was not found")
    return await asyncio.to_thread(store.get_opportunity, user_id, opportunity_id)


@router.post("/opportunities/{opportunity_id}/applications", response_model=StoredOpportunity)
@require_permission("runs", "create")
async def link_opportunity_application(opportunity_id: int, body: OpportunityApplicationLink, request: Request) -> StoredOpportunity:
    del request
    user_id = get_effective_user_id()
    store = _tracker_store()
    try:
        linked = await asyncio.to_thread(store.link_opportunity_application, user_id, opportunity_id, body.application_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not linked:
        raise HTTPException(status_code=404, detail="Opportunity or application was not found")
    return await asyncio.to_thread(store.get_opportunity, user_id, opportunity_id)


@router.get("/match-candidates/{thread_id}", response_model=list[StoredMatchCandidate])
@require_permission("runs", "read")
async def list_match_candidates(thread_id: ThreadId, request: Request) -> list[StoredMatchCandidate]:
    user_id = get_effective_user_id()
    if await get_thread_store(request).get(thread_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Thread was not found")
    return await asyncio.to_thread(_tracker_store().list_match_candidates, user_id, thread_id)


@router.put("/match-candidates/{thread_id}", response_model=list[StoredMatchCandidate])
@require_permission("runs", "create")
async def replace_match_candidates(thread_id: ThreadId, body: MatchCandidatesWrite, request: Request) -> list[StoredMatchCandidate]:
    user_id = get_effective_user_id()
    if await get_thread_store(request).get(thread_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Thread was not found")
    try:
        return await asyncio.to_thread(
            _tracker_store().replace_match_candidates,
            user_id,
            thread_id,
            source_url=body.source_url,
            source_table_id=body.source_table_id,
            candidates=[candidate.model_dump() for candidate in body.candidates],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    writer.writerow(["公司", "岗位", "投递日期", "投递日期原文", "自定义环节", "识别状态", "页面原始状态", "查询链接", "识别结果", "最近检查时间", "置信度", "证据"])
    for row in rows:
        writer.writerow(
            [
                row.company,
                row.role,
                row.applied_at or "",
                row.applied_at_evidence,
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
