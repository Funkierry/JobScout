"""Synthetic pages retain global counts and owner isolation across mutations."""

import json

import pytest
from fastapi.testclient import TestClient

from app.application_tracker.models import ApplicationInput
from app.application_tracker.store import ApplicationTrackerStore


@pytest.fixture
def tracker(tmp_path):
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    store.import_applications("owner", [ApplicationInput(company="Example", role=f"Role {i}", url=f"https://jobs.example.com/{i}") for i in range(123)])
    store.add_application("foreign", ApplicationInput(company="Private", role="Private", url="https://jobs.example.com/private"))
    return store


def test_pages_are_bounded_and_cursor_survives_inserts_and_deletes(tracker, monkeypatch):
    monkeypatch.setattr(tracker, "list_applications", lambda *args: pytest.fail("Do not load complete rows before slicing"))
    first = tracker.list_applications_page("owner", limit=50)
    assert len(first["items"]) == 50 and first["total"] == 123 and first["has_more"]
    assert first["summary"] == {"total": 123, "active": 0, "review": 123, "offers": 0}
    assert first["items"][0].id == 123
    tracker.delete_application("owner", 123)
    tracker.add_application("owner", ApplicationInput(company="New", role="New", url="https://jobs.example.com/new"))
    second = tracker.list_applications_page("owner", limit=50, before_id=first["next_before_id"])
    third = tracker.list_applications_page("owner", limit=50, before_id=second["next_before_id"])
    ids = [row.id for page in (first, second, third) for row in page["items"]]
    assert ids == list(range(123, 0, -1)) and not third["has_more"]
    assert all(row.company != "Private" for page in (first, second, third) for row in page["items"])


def test_filter_and_summary_include_mail_and_manual_stages(tracker):
    with tracker._connect() as connection:
        connection.execute("UPDATE applications SET stage='准备材料',stage_manual=1 WHERE id=1")
        event = {"application_id": 2, "status": "Offer", "review_reason": None, "received_at": "2026-10-05T00:00:00+00:00", "quote": "Synthetic Offer", "event_at": None, "time_quote": ""}
        connection.execute("INSERT INTO jobscout_mail_events(user_id,application_id,fingerprint,event_json,received_at) VALUES(?,?,?,?,?)", ("owner", 2, "fixture", json.dumps(event), event["received_at"]))
    result = tracker.list_applications_page("owner", stage="Offer", limit=10)
    assert [row.id for row in result["items"]] == [2]
    assert result["items"][0].source_summary["source"] == "email"
    assert result["summary"] == {"total": 123, "active": 0, "review": 122, "offers": 1}
    assert result["filtered_total"] == 1
    assert {item["label"]: item["count"] for item in result["stages"]} == {"未知": 121, "准备材料": 1, "Offer": 1}
    assert [row.id for row in tracker.list_applications_page("owner", stage="准备材料")["items"]] == [1]
    assert tracker.list_applications_page("owner", stage="Missing")["items"] == []


def test_page_only_materializes_selected_full_rows(tracker, monkeypatch):
    original = tracker._application_from_row
    materialized = []

    def record(row):
        materialized.append(row["id"])
        return original(row)

    monkeypatch.setattr(tracker, "_application_from_row", record)
    page = tracker.list_applications_page("owner", limit=3)
    assert materialized == [123, 122, 121]
    assert page["total"] == 123


def test_conflicting_mail_has_identical_page_and_full_list_view(tracker):
    with tracker._connect() as connection:
        for status in ("Offer", "未通过"):
            event = {"application_id": 123, "status": status, "review_reason": None, "received_at": "2026-10-05T00:00:00+00:00", "quote": "Synthetic " + status}
            connection.execute("INSERT INTO jobscout_mail_events(user_id,application_id,fingerprint,event_json,received_at) VALUES(?,?,?,?,?)", ("owner", 123, status, json.dumps(event), event["received_at"]))
    page = tracker.list_applications_page("owner", limit=1, stage="未知")
    full = next(row for row in tracker.list_applications("owner") if row.id == 123)
    assert page["items"][0] == full
    assert full.source_summary["conflict"]
    assert page["summary"]["review"] == 123
    assert page["summary"]["offers"] == 0


def test_page_api_bounds_and_auth(tracker, monkeypatch):
    from _router_auth_helpers import make_authed_test_app

    from app.gateway.routers import jobscout

    monkeypatch.setattr(jobscout, "_tracker_store", lambda: tracker)
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "owner")
    app = make_authed_test_app()
    app.include_router(jobscout.router)
    client = TestClient(app)
    path = "/api/jobscout/tracker/applications/page"
    assert client.get(path + "?limit=101").status_code == 422
    assert client.get(path + "?limit=0").status_code == 422
    assert client.get(path + "?before_id=-1").status_code == 422
    response = client.get(path + "?limit=3")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 3 and response.json()["total"] == 123
