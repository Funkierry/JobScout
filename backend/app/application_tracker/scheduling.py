"""JobScout refresh settings, atomic daily budgets and durable in-app notices."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobscout_refresh_settings (user_id TEXT PRIMARY KEY, settings_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobscout_refresh_budget (user_id TEXT NOT NULL, day TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,day));
CREATE TABLE IF NOT EXISTS jobscout_refresh_attempts (
 user_id TEXT NOT NULL, occurrence_id TEXT NOT NULL, application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
 attempted_at TEXT NOT NULL, PRIMARY KEY(user_id,occurrence_id,application_id));
CREATE INDEX IF NOT EXISTS idx_refresh_recent ON jobscout_refresh_attempts(user_id,application_id,attempted_at);
CREATE TABLE IF NOT EXISTS jobscout_notifications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
 application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
 old_status TEXT NOT NULL, new_status TEXT NOT NULL, evidence TEXT NOT NULL,
 source TEXT NOT NULL, source_id TEXT NOT NULL, created_at TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0,
 UNIQUE(user_id,source,source_id,application_id));
CREATE INDEX IF NOT EXISTS idx_notifications_owner ON jobscout_notifications(user_id,id);
CREATE TRIGGER IF NOT EXISTS jobscout_portal_change_notification AFTER INSERT ON application_checks
WHEN NEW.status_changed=1 AND NEW.check_result='成功' AND NEW.old_status IS NOT NULL
 AND NEW.old_status!='未知' AND NEW.new_status!='未知' AND NEW.old_status!=NEW.new_status AND NEW.evidence!=''
BEGIN
 INSERT OR IGNORE INTO jobscout_notifications(user_id,application_id,old_status,new_status,evidence,source,source_id,created_at)
 VALUES(NEW.user_id,NEW.application_id,NEW.old_status,NEW.new_status,NEW.evidence,'portal',CAST(NEW.id AS TEXT),NEW.checked_at);
END;
"""


class RefreshSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    interval_minutes: int = Field(default=360, ge=60, le=10080)
    daily_limit: int = Field(default=30, ge=1, le=100)
    timezone: str = "Asia/Shanghai"
    allow_model_fallback: bool = False

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown timezone") from exc
        return value


def task_id_for(user_id: str) -> str:
    return "jobscout-refresh-" + sha256(user_id.encode()).hexdigest()[:24]


class SchedulingStore:
    def __init__(self, tracker):
        self.tracker = tracker

    def settings(self, user_id: str) -> RefreshSettings:
        with self.tracker._connect() as connection:
            row = connection.execute("SELECT settings_json FROM jobscout_refresh_settings WHERE user_id=?", (self.tracker._validated_user_id(user_id),)).fetchone()
        return RefreshSettings.model_validate_json(row["settings_json"]) if row else RefreshSettings()

    def configure(self, user_id: str, settings: RefreshSettings):
        with self.tracker._connect() as connection:
            connection.execute(
                "INSERT INTO jobscout_refresh_settings(user_id,settings_json) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json", (self.tracker._validated_user_id(user_id), settings.model_dump_json())
            )

    def reserve(self, user_id: str, occurrence_id: str, application_id: int, *, now: datetime) -> bool:
        user_id = self.tracker._validated_user_id(user_id)
        if not occurrence_id or len(occurrence_id) > 200 or now.tzinfo is None:
            raise ValueError("Invalid scheduled occurrence")
        now = now.astimezone(UTC)
        with self.tracker._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT settings_json FROM jobscout_refresh_settings WHERE user_id=?", (user_id,)).fetchone()
            settings = RefreshSettings.model_validate_json(row["settings_json"]) if row else RefreshSettings()
            if not settings.enabled or not connection.execute("SELECT 1 FROM applications WHERE user_id=? AND id=?", (user_id, application_id)).fetchone():
                return False
            if connection.execute("SELECT 1 FROM jobscout_refresh_attempts WHERE user_id=? AND occurrence_id=? AND application_id=?", (user_id, occurrence_id, application_id)).fetchone():
                return False
            earliest = (now - timedelta(minutes=settings.interval_minutes)).isoformat()
            if connection.execute("SELECT 1 FROM jobscout_refresh_attempts WHERE user_id=? AND application_id=? AND attempted_at>?", (user_id, application_id, earliest)).fetchone():
                return False
            day = now.astimezone(ZoneInfo(settings.timezone)).date().isoformat()
            connection.execute("INSERT OR IGNORE INTO jobscout_refresh_budget(user_id,day,used) VALUES(?,?,0)", (user_id, day))
            result = connection.execute("UPDATE jobscout_refresh_budget SET used=used+1 WHERE user_id=? AND day=? AND used<?", (user_id, day, settings.daily_limit))
            if not result.rowcount:
                return False
            connection.execute("INSERT INTO jobscout_refresh_attempts(user_id,occurrence_id,application_id,attempted_at) VALUES(?,?,?,?)", (user_id, occurrence_id, application_id, now.isoformat()))
        return True

    def usage(self, user_id: str, *, now: datetime | None = None) -> int:
        settings = self.settings(user_id)
        day = (now or datetime.now(UTC)).astimezone(ZoneInfo(settings.timezone)).date().isoformat()
        with self.tracker._connect() as connection:
            row = connection.execute("SELECT used FROM jobscout_refresh_budget WHERE user_id=? AND day=?", (user_id, day)).fetchone()
        return row["used"] if row else 0

    def notifications(self, user_id: str) -> list[dict]:
        with self.tracker._connect() as connection:
            rows = connection.execute(
                "SELECT n.*,a.company,a.role FROM jobscout_notifications n JOIN applications a ON a.id=n.application_id AND a.user_id=n.user_id WHERE n.user_id=? ORDER BY n.id DESC LIMIT 100", (self.tracker._validated_user_id(user_id),)
            ).fetchall()
        return [
            {
                "id": r["id"],
                "application_id": r["application_id"],
                "company": r["company"],
                "role": r["role"],
                "old_status": r["old_status"],
                "new_status": r["new_status"],
                "evidence": r["evidence"],
                "source": r["source"],
                "created_at": r["created_at"],
                "read": bool(r["is_read"]),
            }
            for r in rows
        ]

    def mark_read(self, user_id: str, notification_id: int) -> bool:
        with self.tracker._connect() as connection:
            result = connection.execute("UPDATE jobscout_notifications SET is_read=1 WHERE user_id=? AND id=?", (self.tracker._validated_user_id(user_id), notification_id))
        return result.rowcount > 0


def new_scheduled_agent(settings):
    from app.application_tracker.agent.workflow import AgentRunConfig, ApplicationTrackerAgent

    return ApplicationTrackerAgent.from_model_name(run_config=AgentRunConfig(allow_model_fallback=settings.allow_model_fallback))


async def run_scheduled_refresh(tracker, user_id: str, occurrence_id: str, *, agent_factory=None, now: datetime | None = None) -> dict:
    from app.application_tracker.models import CheckResult
    from app.application_tracker.update_service import TrackerUpdateService

    store = SchedulingStore(tracker)
    settings = await asyncio.to_thread(store.settings, user_id)
    summary = {"checked": 0, "failed": 0, "skipped": 0}
    if not settings.enabled:
        return summary
    rows = await asyncio.to_thread(tracker.list_applications, user_id)
    rows = sorted((row for row in rows if not row.terminal), key=lambda row: (row.checked_at or datetime.min.replace(tzinfo=UTC), row.id))
    agent = None
    try:
        for row in rows:
            if not await asyncio.to_thread(store.reserve, user_id, occurrence_id, row.id, now=now or datetime.now(UTC)):
                summary["skipped"] += 1
                continue
            if agent is None:
                agent = (agent_factory or new_scheduled_agent)(settings)
            service = TrackerUpdateService(store=tracker, agent=agent)
            current = await asyncio.to_thread(tracker.get_application, user_id, row.id)
            if current is None or current.terminal:
                summary["skipped"] += 1
                continue
            outcome = await service.refresh_one(user_id, current.id, interactive_login=False)
            if outcome.skipped:
                summary["skipped"] += 1
                continue
            summary["checked"] += 1
            summary["failed"] += outcome.application.check_result is not CheckResult.SUCCESS
    finally:
        if agent is not None:
            await agent.aclose()
    return summary
