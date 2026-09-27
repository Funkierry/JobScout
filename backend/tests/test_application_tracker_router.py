from __future__ import annotations

import json
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile

from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
    StatusRecord,
)
from app.application_tracker.store import ApplicationTrackerStore
from app.gateway.routers import jobscout

pytestmark = pytest.mark.asyncio


class FakeAgent:
    def __init__(self) -> None:
        self.closed = False

    async def run(
        self,
        application: ApplicationInput,
        **kwargs: Any,
    ) -> StatusRecord:
        del kwargs
        checked_at = datetime(2026, 9, 25, 4, 0, tzinfo=UTC)
        return StatusRecord(
            company=application.company,
            role=application.role,
            url=application.url,
            status=ApplicationStatus.ASSESSMENT,
            raw_status="Assessment",
            confidence=0.91,
            evidence="Current status: Assessment",
            checked_at=checked_at,
            changed_at=checked_at,
            check_result=CheckResult.SUCCESS,
        )

    async def aclose(self) -> None:
        self.closed = True


def _install_tracker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[ApplicationTrackerStore, FakeAgent]:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    agent = FakeAgent()
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "user-1")
    monkeypatch.setattr(jobscout, "_tracker_store", lambda: store)
    monkeypatch.setattr(jobscout, "_new_tracker_agent", lambda: agent)
    return store, agent


async def test_opportunity_endpoints_link_only_owned_threads_and_applications(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, _ = _install_tracker(monkeypatch, tmp_path)
    application = store.add_application("user-1", ApplicationInput(company="Example", role="AI PM", url="https://jobs.example.com/1"))

    class ThreadStore:
        async def get(self, thread_id: str, *, user_id: str) -> dict | None:
            return {"thread_id": thread_id} if thread_id == "owned-thread" and user_id == "user-1" else None

    monkeypatch.setattr(jobscout, "get_thread_store", lambda request: ThreadStore())
    opportunity = await jobscout.create_opportunity.__wrapped__(
        body=jobscout.OpportunityCreate(company="Example", role="AI PM"),
        request=None,
    )
    with pytest.raises(HTTPException) as foreign_thread:
        await jobscout.link_opportunity_thread.__wrapped__(
            opportunity_id=opportunity.id,
            body=jobscout.OpportunityThreadLink(thread_id="foreign-thread", mode="prep"),
            request=None,
        )
    assert foreign_thread.value.status_code == 404
    linked_thread = await jobscout.link_opportunity_thread.__wrapped__(
        opportunity_id=opportunity.id,
        body=jobscout.OpportunityThreadLink(thread_id="owned-thread", mode="prep"),
        request=None,
    )
    assert linked_thread.prep_thread_id == "owned-thread"
    linked_application = await jobscout.link_opportunity_application.__wrapped__(
        opportunity_id=opportunity.id,
        body=jobscout.OpportunityApplicationLink(application_id=application.id),
        request=None,
    )
    assert linked_application.application_ids == [application.id]
    assert [row.id for row in await jobscout.list_opportunities.__wrapped__(request=None)] == [opportunity.id]


async def test_match_candidates_are_saved_only_for_an_owned_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_tracker(monkeypatch, tmp_path)

    class ThreadStore:
        async def get(self, thread_id: str, *, user_id: str) -> dict | None:
            return {"thread_id": thread_id} if thread_id == "owned-thread" and user_id == "user-1" else None

    monkeypatch.setattr(jobscout, "get_thread_store", lambda request: ThreadStore())
    body = jobscout.MatchCandidatesWrite(
        source_url="https://example.feishu.cn/base/abc",
        source_table_id="tblJobs",
        candidates=[jobscout.MatchCandidateWrite(record_id="rec1", company="Example", role="AI PM")],
    )
    with pytest.raises(HTTPException) as foreign_thread:
        await jobscout.replace_match_candidates.__wrapped__(thread_id="foreign-thread", body=body, request=None)
    assert foreign_thread.value.status_code == 404
    saved = await jobscout.replace_match_candidates.__wrapped__(thread_id="owned-thread", body=body, request=None)
    assert [item.record_id for item in saved] == ["rec1"]
    restored = await jobscout.list_match_candidates.__wrapped__(thread_id="owned-thread", request=None)
    assert restored == saved


async def test_tracker_csv_import_and_list_use_current_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, _ = _install_tracker(monkeypatch, tmp_path)
    file = UploadFile(
        filename="applications.csv",
        file=BytesIO(b"company,role,url,applied_at,notes\nExample,AI PM,https://jobs.example.com/1,2026-09-01,Campus\n"),
    )

    summary = await jobscout.import_tracker_csv.__wrapped__(file=file, request=None)
    rows = await jobscout.list_tracker_applications.__wrapped__(request=None)

    assert summary.inserted == 1
    assert len(rows) == 1
    assert rows[0].company == "Example"
    assert store.list_applications("user-2") == []


async def test_tracker_export_includes_official_date_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, _ = _install_tracker(monkeypatch, tmp_path)
    row = store.add_application("user-1", ApplicationInput(company="Example", role="AI PM", url="https://jobs.example.com/1"))
    checked_at = datetime(2026, 9, 25, 4, 0, tzinfo=UTC)
    store.save_check(
        "user-1",
        row.id,
        StatusRecord(
            company=row.company,
            role=row.role,
            url=row.url,
            status=ApplicationStatus.APPLIED,
            raw_status="已投递",
            confidence=0.9,
            evidence="当前状态：已投递",
            applied_at=date(2026, 9, 20),
            applied_at_evidence="投递时间：2026-09-20",
            checked_at=checked_at,
            changed_at=checked_at,
            check_result=CheckResult.SUCCESS,
        ),
    )

    response = await jobscout.export_tracker_csv.__wrapped__(request=None)
    csv_text = response.body.decode("utf-8-sig")

    assert "投递日期原文" in csv_text
    assert "投递时间：2026-09-20" in csv_text


async def test_tracker_single_refresh_maps_missing_row_to_404(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, agent = _install_tracker(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await jobscout.refresh_tracker_application.__wrapped__(
            application_id=999,
            request=None,
        )

    assert exc_info.value.status_code == 404
    assert agent.closed


async def test_tracker_batch_refresh_streams_json_sse_and_closes_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, agent = _install_tracker(monkeypatch, tmp_path)
    store.import_applications(
        "user-1",
        [ApplicationInput(company="Example", role="AI PM", url="https://jobs.example.com/1")],
    )

    response = await jobscout.refresh_all_tracker_applications.__wrapped__(request=None)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    payloads = [json.loads(line.removeprefix("data: ")) for line in "".join(chunks).splitlines() if line.startswith("data: ")]

    assert response.media_type == "text/event-stream"
    assert payloads[0]["type"] == "batch_started"
    assert payloads[-1] == {
        "type": "batch_completed",
        "index": 0,
        "total": 1,
        "completed": 1,
        "application_id": None,
        "company": None,
        "message": None,
        "application": None,
    }
    assert agent.closed
