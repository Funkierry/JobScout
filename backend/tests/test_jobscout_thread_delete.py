"""JobScout deletion wraps native permissions/lifecycle, using synthetic data."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.application_tracker.models import ApplicationInput
from app.application_tracker.store import ApplicationTrackerStore
from app.gateway.routers import jobscout, threads
from app.jobscout.inputs import BaseSnapshotStore


@pytest.fixture
def deletion(tmp_path, monkeypatch):
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    opportunity = store.create_opportunity("owner", company="Fixture", role="Engineer")
    application = store.add_application("owner", ApplicationInput(company="Fixture", role="Engineer", url="https://jobs.example.com/application"))
    store.link_opportunity_application("owner", opportunity.id, application.id)
    store.link_opportunity_thread("owner", opportunity.id, "delete-me", "prep")
    store.link_opportunity_thread("owner", opportunity.id, "keep-me", "match")
    store.replace_match_candidates("owner", "delete-me", source_url="https://example.feishu.cn/base/test", source_table_id="table", candidates=[{"record_id": "r", "company": "Fixture", "role": "Engineer"}])
    snapshots = BaseSnapshotStore()
    ref = snapshots.put("owner", "delete-me", [{"record_id": "r"}])
    other_ref = snapshots.put("other", "delete-me", [{"record_id": "other"}])
    state = {"exists": True, "error": None}

    async def get(thread_id, *, user_id):
        return {"thread_id": thread_id} if user_id == "owner" and state["exists"] else None

    async def native(*, thread_id, request):
        if state["error"]:
            raise HTTPException(state["error"], "Native deletion rejected")
        state["exists"] = False
        return threads.ThreadDeleteResponse(success=True, message="Deleted")

    native_delete = AsyncMock(side_effect=native)
    monkeypatch.setattr(jobscout, "_tracker_store", lambda: store)
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "owner")
    monkeypatch.setattr(jobscout, "get_thread_store", lambda _: SimpleNamespace(get=get))
    monkeypatch.setattr(jobscout, "BASE_SNAPSHOTS", snapshots)
    monkeypatch.setattr(threads, "delete_thread_data", native_delete)
    return SimpleNamespace(store=store, opportunity=opportunity, application=application, snapshots=snapshots, ref=ref, other_ref=other_ref, state=state, native=native_delete)


@pytest.mark.asyncio
async def test_deletes_only_owned_thread_references_and_preserves_targets(deletion):
    result = await jobscout.delete_jobscout_thread.__wrapped__(thread_id="delete-me", request=None)
    assert result["deleted"] is True
    deletion.native.assert_awaited_once_with(thread_id="delete-me", request=None)
    target = deletion.store.get_opportunity("owner", deletion.opportunity.id)
    assert target.prep_thread_id is None and target.match_thread_id == "keep-me"
    assert target.application_ids == [deletion.application.id]
    assert deletion.store.get_application("owner", deletion.application.id)
    assert not deletion.store.list_match_candidates("owner", "delete-me")
    assert deletion.snapshots.get(deletion.ref, "owner", "delete-me") is None
    assert deletion.snapshots.get(deletion.other_ref, "other", "delete-me")
    assert not deletion.store.thread_deletion_pending("owner", "delete-me")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 409, 500])
async def test_native_failure_keeps_links_and_does_not_claim_deleted(deletion, status):
    deletion.state["error"] = status
    with pytest.raises(HTTPException) as error:
        await jobscout.delete_jobscout_thread.__wrapped__(thread_id="delete-me", request=None)
    assert error.value.status_code == status
    assert deletion.store.get_opportunity("owner", deletion.opportunity.id).prep_thread_id == "delete-me"
    assert deletion.snapshots.get(deletion.ref, "owner", "delete-me")


@pytest.mark.asyncio
async def test_foreign_or_missing_thread_cannot_reach_native_delete(deletion, monkeypatch):
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "other")
    with pytest.raises(HTTPException) as error:
        await jobscout.delete_jobscout_thread.__wrapped__(thread_id="delete-me", request=None)
    assert error.value.status_code == 404
    deletion.native.assert_not_awaited()
    assert deletion.store.get_opportunity("owner", deletion.opportunity.id).prep_thread_id == "delete-me"


@pytest.mark.asyncio
async def test_cleanup_can_retry_after_native_thread_is_gone(deletion, monkeypatch):
    cleanup = deletion.store.finish_thread_deletion
    monkeypatch.setattr(deletion.store, "finish_thread_deletion", lambda *args: (_ for _ in ()).throw(OSError("fixture")))
    with pytest.raises(HTTPException) as error:
        await jobscout.delete_jobscout_thread.__wrapped__(thread_id="delete-me", request=None)
    assert error.value.status_code == 503
    assert deletion.store.thread_deletion_pending("owner", "delete-me")
    monkeypatch.setattr(deletion.store, "finish_thread_deletion", cleanup)
    assert (await jobscout.delete_jobscout_thread.__wrapped__(thread_id="delete-me", request=None))["deleted"]
    assert deletion.native.await_count == 1
    assert deletion.store.get_opportunity("owner", deletion.opportunity.id).prep_thread_id is None


@pytest.mark.asyncio
async def test_metadata_delete_failure_is_visible_and_keeps_references(deletion):
    deletion.native.side_effect = None
    deletion.native.return_value = threads.ThreadDeleteResponse(success=True, message="Local data removed")
    with pytest.raises(HTTPException) as error:
        await jobscout.delete_jobscout_thread.__wrapped__(thread_id="delete-me", request=None)
    assert error.value.status_code == 503
    assert deletion.store.get_opportunity("owner", deletion.opportunity.id).prep_thread_id == "delete-me"


@pytest.mark.asyncio
async def test_route_requires_thread_delete_permission(deletion):
    request = SimpleNamespace(state=SimpleNamespace(auth=SimpleNamespace(is_authenticated=True, has_permission=lambda *_: False)))
    with pytest.raises(HTTPException) as error:
        await jobscout.delete_jobscout_thread(thread_id="delete-me", request=request)
    assert error.value.status_code == 403
    deletion.native.assert_not_awaited()


def test_cleanup_is_owner_scoped_and_idempotent(deletion):
    deletion.store.begin_thread_deletion("owner", "delete-me")
    deletion.store.finish_thread_deletion("other", "delete-me")
    assert deletion.store.thread_deletion_pending("owner", "delete-me")
    assert deletion.store.list_match_candidates("owner", "delete-me")
    deletion.store.finish_thread_deletion("owner", "delete-me")
    deletion.store.finish_thread_deletion("owner", "delete-me")
    assert not deletion.store.list_match_candidates("owner", "delete-me")
    assert not deletion.store.thread_deletion_pending("owner", "delete-me")


def test_cleanup_rolls_back_links_and_retry_marker_on_database_failure(deletion):
    deletion.store.begin_thread_deletion("owner", "delete-me")
    with deletion.store._connect() as connection:
        connection.execute("CREATE TRIGGER fail_cleanup BEFORE DELETE ON jobscout_match_candidates BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        deletion.store.finish_thread_deletion("owner", "delete-me")
    assert deletion.store.get_opportunity("owner", deletion.opportunity.id).prep_thread_id == "delete-me"
    assert deletion.store.list_match_candidates("owner", "delete-me")
    assert deletion.store.thread_deletion_pending("owner", "delete-me")
    with deletion.store._connect() as connection:
        connection.execute("DROP TRIGGER fail_cleanup")
    deletion.store.finish_thread_deletion("owner", "delete-me")
    assert not deletion.store.thread_deletion_pending("owner", "delete-me")
