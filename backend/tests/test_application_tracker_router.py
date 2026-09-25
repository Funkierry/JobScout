from __future__ import annotations

import json
from datetime import UTC, datetime
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
