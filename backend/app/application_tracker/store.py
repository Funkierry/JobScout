"""User-isolated SQLite storage for applications and check history."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
    StatusRecord,
)
from deerflow.config.paths import get_paths, resolve_path

TERMINAL_STATUSES = {
    ApplicationStatus.OFFER,
    ApplicationStatus.REJECTED,
    ApplicationStatus.TERMINATED,
}


class ImportSummary(BaseModel):
    inserted: int
    updated: int
    total: int


class StoredApplication(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company: str
    role: str
    url: str
    applied_at: date | None
    applied_at_evidence: str
    notes: str
    status: ApplicationStatus
    stage: str = ""
    stage_manual: bool = False
    role_confirmed: bool = True
    raw_status: str
    confidence: float
    evidence: str
    checked_at: datetime | None
    changed_at: datetime | None
    check_result: CheckResult | None
    previous_status: ApplicationStatus | None = None
    changed: bool = False
    created_at: datetime
    updated_at: datetime
    source_summary: dict | None = None

    @computed_field
    @property
    def terminal(self) -> bool:
        if self.source_summary and not self.source_summary.get("conflict") and self.source_summary.get("source") == "email":
            return self.source_summary.get("status") in {item.value for item in TERMINAL_STATUSES}
        return self.status in TERMINAL_STATUSES

    def to_input(self) -> ApplicationInput:
        return ApplicationInput(
            company=self.company,
            role=self.role,
            url=self.url,
            applied_at=self.applied_at,
            notes=self.notes,
        )

    def to_previous_record(self) -> StatusRecord | None:
        if self.checked_at is None or self.changed_at is None or self.check_result is None:
            return None
        return StatusRecord(
            company=self.company,
            role=self.role,
            url=self.url,
            status=self.status,
            raw_status=self.raw_status,
            confidence=self.confidence,
            evidence=self.evidence,
            applied_at=self.applied_at,
            applied_at_evidence=self.applied_at_evidence,
            checked_at=self.checked_at,
            changed_at=self.changed_at,
            check_result=self.check_result,
        )


class StoredCheck(BaseModel):
    id: int
    application_id: int
    old_status: ApplicationStatus | None
    new_status: ApplicationStatus
    raw_status: str
    confidence: float
    evidence: str
    check_result: CheckResult
    checked_at: datetime
    changed_at: datetime
    status_changed: bool


class StoredOpportunity(BaseModel):
    id: int
    company: str
    role: str
    recruitment_type: str
    source_kind: str
    source_url: str
    source_table_id: str
    source_record_id: str
    jd_text: str
    prep_thread_id: str | None = None
    match_thread_id: str | None = None
    application_ids: list[int] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class StoredMatchCandidate(BaseModel):
    record_id: str
    company: str
    role: str
    jd_text: str
    source_url: str
    source_table_id: str


def default_database_path() -> Path:
    configured = os.getenv("APPLICATION_TRACKER_DB_PATH")
    if configured:
        return resolve_path(configured)
    return get_paths().base_dir / "application-tracker" / "tracker.db"


class ApplicationTrackerStore:
    """Small synchronous repository; async callers should use ``to_thread``."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        from app.application_tracker.email.store import SCHEMA as MAIL_SCHEMA
        from app.application_tracker.scheduling import SCHEMA as SCHEDULING_SCHEMA

        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    company TEXT NOT NULL,
                    role TEXT NOT NULL,
                    url TEXT NOT NULL,
                    applied_at TEXT,
                    applied_at_evidence TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    stage_manual INTEGER NOT NULL DEFAULT 0,
                    role_confirmed INTEGER NOT NULL DEFAULT 1,
                    raw_status TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    evidence TEXT NOT NULL DEFAULT '',
                    checked_at TEXT,
                    changed_at TEXT,
                    check_result TEXT,
                    previous_status TEXT,
                    last_check_changed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, url)
                );
                CREATE INDEX IF NOT EXISTS idx_applications_user
                    ON applications(user_id, id);
                CREATE TABLE IF NOT EXISTS application_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    old_status TEXT,
                    new_status TEXT NOT NULL,
                    raw_status TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    check_result TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    status_changed INTEGER NOT NULL,
                    FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_checks_user_application
                    ON application_checks(user_id, application_id, id);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(applications)")}
            for name, declaration in (
                ("stage", "TEXT NOT NULL DEFAULT ''"),
                ("stage_manual", "INTEGER NOT NULL DEFAULT 0"),
                ("role_confirmed", "INTEGER NOT NULL DEFAULT 1"),
                ("applied_at_evidence", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE applications ADD COLUMN {name} {declaration}")
            connection.execute("UPDATE applications SET stage = status WHERE stage = ''")
            connection.executescript(MAIL_SCHEMA)
            connection.executescript(SCHEDULING_SCHEMA)
            connection.execute("CREATE TABLE IF NOT EXISTS application_stages (user_id TEXT NOT NULL, name TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(user_id, name), UNIQUE(user_id, position))")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobscout_opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    company TEXT NOT NULL,
                    role TEXT NOT NULL,
                    recruitment_type TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT 'manual',
                    source_url TEXT NOT NULL DEFAULT '',
                    source_table_id TEXT NOT NULL DEFAULT '',
                    source_record_id TEXT NOT NULL DEFAULT '',
                    source_identity TEXT,
                    jd_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, source_identity)
                );
                CREATE INDEX IF NOT EXISTS idx_jobscout_opportunities_user
                    ON jobscout_opportunities(user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS jobscout_opportunity_threads (
                    thread_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    opportunity_id INTEGER NOT NULL REFERENCES jobscout_opportunities(id) ON DELETE CASCADE,
                    mode TEXT NOT NULL CHECK(mode IN ('prep', 'match')),
                    linked_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobscout_opportunity_threads
                    ON jobscout_opportunity_threads(user_id, opportunity_id, mode, linked_at DESC);
                CREATE TABLE IF NOT EXISTS jobscout_opportunity_applications (
                    application_id INTEGER PRIMARY KEY REFERENCES applications(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    opportunity_id INTEGER NOT NULL REFERENCES jobscout_opportunities(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_jobscout_opportunity_applications
                    ON jobscout_opportunity_applications(user_id, opportunity_id);
                CREATE TABLE IF NOT EXISTS jobscout_match_candidates (
                    thread_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    company TEXT NOT NULL,
                    role TEXT NOT NULL,
                    jd_text TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL,
                    source_table_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY(thread_id, record_id)
                );
                CREATE INDEX IF NOT EXISTS idx_jobscout_match_candidates_user
                    ON jobscout_match_candidates(user_id, thread_id, position);
                """
            )

    def replace_match_candidates(
        self,
        user_id: str,
        thread_id: str,
        *,
        source_url: str,
        source_table_id: str,
        candidates: list[dict[str, str]],
    ) -> list[StoredMatchCandidate]:
        user_id = self._validated_user_id(user_id)
        if not thread_id or not source_url or not source_table_id or len(candidates) > 10:
            raise ValueError("Invalid match candidates")
        ids = [candidate.get("record_id", "") for candidate in candidates]
        if len(set(ids)) != len(ids) or any(not value or len(value) > 200 for value in ids):
            raise ValueError("Match candidates need unique record IDs")
        with self._connect() as connection:
            connection.execute("DELETE FROM jobscout_match_candidates WHERE user_id = ? AND thread_id = ?", (user_id, thread_id))
            connection.executemany(
                """INSERT INTO jobscout_match_candidates
                   (thread_id, user_id, record_id, company, role, jd_text, source_url, source_table_id, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(thread_id, user_id, candidate["record_id"], candidate["company"], candidate["role"], candidate.get("jd_text", ""), source_url, source_table_id, index) for index, candidate in enumerate(candidates)],
            )
        return self.list_match_candidates(user_id, thread_id)

    def list_match_candidates(self, user_id: str, thread_id: str) -> list[StoredMatchCandidate]:
        user_id = self._validated_user_id(user_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobscout_match_candidates WHERE user_id = ? AND thread_id = ? ORDER BY position",
                (user_id, thread_id),
            ).fetchall()
        return [
            StoredMatchCandidate(
                record_id=row["record_id"],
                company=row["company"],
                role=row["role"],
                jd_text=row["jd_text"],
                source_url=row["source_url"],
                source_table_id=row["source_table_id"],
            )
            for row in rows
        ]

    def create_opportunity(
        self,
        user_id: str,
        *,
        company: str,
        role: str,
        recruitment_type: str = "",
        source_kind: str = "manual",
        source_url: str = "",
        source_table_id: str = "",
        source_record_id: str = "",
        jd_text: str = "",
    ) -> StoredOpportunity:
        user_id = self._validated_user_id(user_id)
        company, role = company.strip(), role.strip()
        if not company or not role or len(company) > 200 or len(role) > 300:
            raise ValueError("Company and role are required and must fit their limits")
        if source_kind not in {"manual", "feishu", "tracker"}:
            raise ValueError("Unknown opportunity source")
        if source_kind == "feishu" and not (source_url and source_table_id and source_record_id):
            raise ValueError("Feishu source needs URL, table ID and record ID")
        if len(jd_text) > 8000:
            raise ValueError("Job description is too long")
        identity = None
        if source_kind == "feishu":
            identity = sha256(f"{source_url}\n{source_table_id}\n{source_record_id}".encode()).hexdigest()
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO jobscout_opportunities
                   (user_id, company, role, recruitment_type, source_kind, source_url,
                    source_table_id, source_record_id, source_identity, jd_text, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, company, role, recruitment_type, source_kind, source_url, source_table_id, source_record_id, identity, jd_text, now, now),
            )
            if identity:
                row = connection.execute(
                    "SELECT id FROM jobscout_opportunities WHERE user_id = ? AND source_identity = ?",
                    (user_id, identity),
                ).fetchone()
            else:
                row = connection.execute("SELECT last_insert_rowid() AS id").fetchone()
        return self.get_opportunity(user_id, row["id"])

    def list_opportunities(self, user_id: str) -> list[StoredOpportunity]:
        user_id = self._validated_user_id(user_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM jobscout_opportunities WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
                (user_id,),
            ).fetchall()
        return [opportunity for row in rows if (opportunity := self.get_opportunity(user_id, row["id"])) is not None]

    def get_opportunity(self, user_id: str, opportunity_id: int) -> StoredOpportunity | None:
        user_id = self._validated_user_id(user_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobscout_opportunities WHERE user_id = ? AND id = ?",
                (user_id, opportunity_id),
            ).fetchone()
            if row is None:
                return None
            threads = connection.execute(
                "SELECT mode, thread_id FROM jobscout_opportunity_threads WHERE user_id = ? AND opportunity_id = ? ORDER BY linked_at DESC, rowid DESC",
                (user_id, opportunity_id),
            ).fetchall()
            applications = connection.execute(
                "SELECT application_id FROM jobscout_opportunity_applications WHERE user_id = ? AND opportunity_id = ? ORDER BY application_id",
                (user_id, opportunity_id),
            ).fetchall()
        latest_threads = {item["mode"]: item["thread_id"] for item in reversed(threads)}
        return StoredOpportunity(
            id=row["id"],
            company=row["company"],
            role=row["role"],
            recruitment_type=row["recruitment_type"],
            source_kind=row["source_kind"],
            source_url=row["source_url"],
            source_table_id=row["source_table_id"],
            source_record_id=row["source_record_id"],
            jd_text=row["jd_text"],
            prep_thread_id=latest_threads.get("prep"),
            match_thread_id=latest_threads.get("match"),
            application_ids=[item["application_id"] for item in applications],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def link_opportunity_thread(self, user_id: str, opportunity_id: int, thread_id: str, mode: str) -> bool:
        user_id = self._validated_user_id(user_id)
        if mode not in {"prep", "match"} or not thread_id:
            raise ValueError("Invalid opportunity thread")
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM jobscout_opportunities WHERE id = ? AND user_id = ?", (opportunity_id, user_id)).fetchone():
                return False
            existing = connection.execute("SELECT opportunity_id FROM jobscout_opportunity_threads WHERE thread_id = ?", (thread_id,)).fetchone()
            if existing and existing["opportunity_id"] != opportunity_id:
                raise ValueError("Thread is already linked to another opportunity")
            connection.execute(
                "INSERT OR IGNORE INTO jobscout_opportunity_threads(thread_id, user_id, opportunity_id, mode, linked_at) VALUES (?, ?, ?, ?, ?)",
                (thread_id, user_id, opportunity_id, mode, datetime.now(UTC).isoformat()),
            )
        return True

    def link_opportunity_application(self, user_id: str, opportunity_id: int, application_id: int) -> bool:
        user_id = self._validated_user_id(user_id)
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM jobscout_opportunities WHERE id = ? AND user_id = ?", (opportunity_id, user_id)).fetchone():
                return False
            if not connection.execute("SELECT 1 FROM applications WHERE id = ? AND user_id = ?", (application_id, user_id)).fetchone():
                return False
            existing = connection.execute("SELECT opportunity_id FROM jobscout_opportunity_applications WHERE application_id = ?", (application_id,)).fetchone()
            if existing and existing["opportunity_id"] != opportunity_id:
                raise ValueError("Application is already linked to another opportunity")
            connection.execute(
                "INSERT OR IGNORE INTO jobscout_opportunity_applications(application_id, user_id, opportunity_id) VALUES (?, ?, ?)",
                (application_id, user_id, opportunity_id),
            )
        return True

    def delete_opportunity(self, user_id: str, opportunity_id: int) -> bool:
        user_id = self._validated_user_id(user_id)
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM jobscout_opportunities WHERE id = ? AND user_id = ?",
                (opportunity_id, user_id),
            )
        return result.rowcount > 0

    def list_stages(self, user_id: str) -> list[str]:
        user_id = self._validated_user_id(user_id)
        defaults = [status.value for status in ApplicationStatus]
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM application_stages WHERE user_id = ? LIMIT 1", (user_id,)).fetchone():
                connection.executemany("INSERT OR IGNORE INTO application_stages(user_id, name, position) VALUES (?, ?, ?)", [(user_id, name, index) for index, name in enumerate(defaults)])
            return [row["name"] for row in connection.execute("SELECT name FROM application_stages WHERE user_id = ? ORDER BY position", (user_id,))]

    def replace_stages(self, user_id: str, stages: list[str]) -> list[str]:
        user_id = self._validated_user_id(user_id)
        normalized = [stage.strip() for stage in stages]
        if not normalized or any(not stage or len(stage) > 60 for stage in normalized) or len(set(normalized)) != len(normalized) or len(normalized) > 40:
            raise ValueError("Stages must be 1-40 unique non-empty names of at most 60 characters")
        with self._connect() as connection:
            used = {row["stage"] for row in connection.execute("SELECT DISTINCT stage FROM applications WHERE user_id = ?", (user_id,))}
            if not used.issubset(set(normalized)):
                raise ValueError("A stage assigned to an application cannot be removed")
            connection.execute("DELETE FROM application_stages WHERE user_id = ?", (user_id,))
            connection.executemany("INSERT INTO application_stages(user_id, name, position) VALUES (?, ?, ?)", [(user_id, name, index) for index, name in enumerate(normalized)])
        return normalized

    def add_application(self, user_id: str, application: ApplicationInput) -> StoredApplication:
        user_id = self._validated_user_id(user_id)
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO applications(user_id, company, role, url, applied_at, notes, status, stage, role_confirmed, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    application.company,
                    application.role,
                    application.url,
                    application.applied_at.isoformat() if application.applied_at else None,
                    application.notes,
                    ApplicationStatus.UNKNOWN.value,
                    ApplicationStatus.APPLIED.value,
                    int(application.role != "\u5f85\u8bc6\u522b\u5c97\u4f4d"),
                    now,
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM applications WHERE user_id = ? AND url = ?", (user_id, application.url)).fetchone()
        return self._application_from_row(row)

    def update_application(self, user_id: str, application_id: int, values: dict[str, object]) -> StoredApplication | None:
        user_id = self._validated_user_id(user_id)
        allowed = {"company", "role", "url", "applied_at", "notes", "stage"}
        updates = {key: value for key, value in values.items() if key in allowed and value is not None}
        if not updates:
            return self.get_application(user_id, application_id)
        if "role" in updates:
            updates["role_confirmed"] = 1
        if "stage" in updates:
            if updates["stage"] not in self.list_stages(user_id):
                raise ValueError("Unknown application stage")
            updates["stage_manual"] = 1
        if "applied_at" in updates and isinstance(updates["applied_at"], date):
            updates["applied_at"] = updates["applied_at"].isoformat()
            updates["applied_at_evidence"] = ""
        updates["updated_at"] = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            cursor = connection.execute(f"UPDATE applications SET {assignments} WHERE id = ? AND user_id = ?", (*updates.values(), application_id, user_id))
            if not cursor.rowcount:
                return None
        return self.get_application(user_id, application_id)

    def delete_application(self, user_id: str, application_id: int) -> bool:
        user_id = self._validated_user_id(user_id)
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM applications WHERE id = ? AND user_id = ?", (application_id, user_id))
            return bool(cursor.rowcount)

    def import_applications(
        self,
        user_id: str,
        applications: list[ApplicationInput],
    ) -> ImportSummary:
        user_id = self._validated_user_id(user_id)
        inserted = 0
        updated = 0
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            for application in applications:
                existing = connection.execute(
                    "SELECT id FROM applications WHERE user_id = ? AND url = ?",
                    (user_id, application.url),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO applications (
                            user_id, company, role, url, applied_at, notes, status,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
                            application.company,
                            application.role,
                            application.url,
                            application.applied_at.isoformat() if application.applied_at else None,
                            application.notes,
                            ApplicationStatus.UNKNOWN.value,
                            now,
                            now,
                        ),
                    )
                    inserted += 1
                else:
                    connection.execute(
                        """
                        UPDATE applications
                        SET company = ?, role = ?, applied_at = COALESCE(?, applied_at),
                            applied_at_evidence = CASE WHEN ? IS NOT NULL THEN '' ELSE applied_at_evidence END,
                            notes = ?, updated_at = ?
                        WHERE id = ? AND user_id = ?
                        """,
                        (
                            application.company,
                            application.role,
                            application.applied_at.isoformat() if application.applied_at else None,
                            application.applied_at.isoformat() if application.applied_at else None,
                            application.notes,
                            now,
                            existing["id"],
                            user_id,
                        ),
                    )
                    updated += 1
        return ImportSummary(inserted=inserted, updated=updated, total=len(applications))

    def list_applications(self, user_id: str) -> list[StoredApplication]:
        user_id = self._validated_user_id(user_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM applications WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()
        from app.application_tracker.email.store import MailStore

        return MailStore(self).decorate(user_id, [self._application_from_row(row) for row in rows])

    def get_application(self, user_id: str, application_id: int) -> StoredApplication | None:
        user_id = self._validated_user_id(user_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM applications WHERE user_id = ? AND id = ?",
                (user_id, application_id),
            ).fetchone()
        if row is None:
            return None
        from app.application_tracker.email.store import MailStore

        return MailStore(self).decorate(user_id, [self._application_from_row(row)])[0]

    def save_check(
        self,
        user_id: str,
        application_id: int,
        record: StatusRecord,
    ) -> StoredApplication:
        user_id = self._validated_user_id(user_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM applications WHERE user_id = ? AND id = ?",
                (user_id, application_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Application {application_id} was not found")
            if row["url"] != record.url:
                raise ValueError("Check result URL does not match the stored application")

            successful_check = connection.execute(
                """
                SELECT 1 FROM application_checks
                WHERE user_id = ? AND application_id = ? AND check_result = ?
                LIMIT 1
                """,
                (user_id, application_id, CheckResult.SUCCESS.value),
            ).fetchone()
            had_baseline = successful_check is not None
            successful = record.check_result is CheckResult.SUCCESS
            old_status = ApplicationStatus(row["status"]) if had_baseline else None
            discovered = {item.role: item for item in record.discovered_applications}
            primary_role = record.detected_role if record.detected_role and not row["role_confirmed"] else row["role"]
            primary_discovery = discovered.get(primary_role)
            detected_status = primary_discovery.status if primary_discovery else record.status
            status_changed = bool(successful and had_baseline and old_status != detected_status)

            if successful:
                new_status = primary_discovery.status if primary_discovery else record.status
                raw_status = primary_discovery.raw_status if primary_discovery else record.raw_status
                confidence = primary_discovery.confidence if primary_discovery else record.confidence
                evidence = primary_discovery.evidence if primary_discovery else record.evidence
                website_applied_at = primary_discovery.applied_at if primary_discovery and primary_discovery.applied_at else (record.applied_at.isoformat() if record.applied_at else None)
                website_date_evidence = primary_discovery.applied_at_evidence if primary_discovery and primary_discovery.applied_at else record.applied_at_evidence
                applied_at = website_applied_at or row["applied_at"]
                applied_at_evidence = website_date_evidence if website_applied_at else row["applied_at_evidence"]
                changed_at = record.checked_at if status_changed or not had_baseline else self._parse_datetime(row["changed_at"])
                if changed_at is None:
                    changed_at = record.checked_at
                detected_role = record.detected_role if record.detected_role and not row["role_confirmed"] else row["role"]
                detected_role_confirmed = int(bool(record.detected_role) or bool(row["role_confirmed"]))
                stage = new_status.value if not row["stage_manual"] else row["stage"]
            else:
                new_status = ApplicationStatus(row["status"])
                raw_status = row["raw_status"]
                confidence = float(row["confidence"])
                evidence = row["evidence"]
                applied_at = row["applied_at"]
                applied_at_evidence = row["applied_at_evidence"]
                changed_at = self._parse_datetime(row["changed_at"]) or record.checked_at
                detected_role = row["role"]
                detected_role_confirmed = row["role_confirmed"]
                stage = row["stage"]

            connection.execute(
                """
                INSERT INTO application_checks (
                    application_id, user_id, old_status, new_status, raw_status,
                    confidence, evidence, check_result, checked_at, changed_at,
                    status_changed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    application_id,
                    user_id,
                    old_status.value if old_status else None,
                    new_status.value,
                    raw_status if successful else record.raw_status,
                    confidence if successful else record.confidence,
                    evidence if successful else record.evidence,
                    record.check_result.value,
                    record.checked_at.isoformat(),
                    changed_at.isoformat(),
                    int(status_changed),
                ),
            )
            connection.execute(
                """
                UPDATE applications
                SET status = ?, stage = ?, role = ?, role_confirmed = ?, raw_status = ?, confidence = ?, evidence = ?,
                    applied_at = ?, applied_at_evidence = ?,
                    checked_at = ?, changed_at = ?, check_result = ?,
                    previous_status = ?, last_check_changed = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    new_status.value,
                    stage,
                    detected_role,
                    detected_role_confirmed,
                    raw_status,
                    confidence,
                    evidence,
                    applied_at,
                    applied_at_evidence,
                    record.checked_at.isoformat(),
                    changed_at.isoformat(),
                    record.check_result.value,
                    old_status.value if status_changed and old_status else None,
                    int(status_changed),
                    datetime.now(UTC).isoformat(),
                    application_id,
                    user_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM applications WHERE user_id = ? AND id = ?",
                (user_id, application_id),
            ).fetchone()
            if successful:
                replacement_id: int | None = None
                for item in record.discovered_applications:
                    if item.role in {primary_role, "待识别岗位"}:
                        continue
                    parts = urlsplit(record.url)
                    base_fragment = "&".join(part for part in parts.fragment.split("&") if not part.startswith("jobscout-role="))
                    role_fragment = f"{base_fragment + '&' if base_fragment else ''}jobscout-role={quote(item.role, safe='')}"
                    role_url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, role_fragment))
                    if len(role_url) > 2048:
                        continue
                    existing = connection.execute(
                        "SELECT id FROM applications WHERE user_id = ? AND url = ?",
                        (user_id, role_url),
                    ).fetchone()
                    now = datetime.now(UTC).isoformat()
                    if existing is None:
                        connection.execute(
                            """
                            INSERT INTO applications (
                                user_id, company, role, url, applied_at, applied_at_evidence, notes, status, stage,
                                role_confirmed, raw_status, confidence, evidence, checked_at,
                                changed_at, check_result, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                user_id,
                                row["company"],
                                item.role,
                                role_url,
                                item.applied_at or None,
                                item.applied_at_evidence if item.applied_at else "",
                                row["notes"],
                                item.status.value,
                                item.status.value,
                                item.raw_status,
                                item.confidence,
                                item.evidence,
                                record.checked_at.isoformat(),
                                record.checked_at.isoformat(),
                                CheckResult.SUCCESS.value,
                                now,
                                now,
                            ),
                        )
                        child_id = connection.execute("SELECT id FROM applications WHERE user_id = ? AND url = ?", (user_id, role_url)).fetchone()["id"]
                        connection.execute(
                            """
                            INSERT INTO application_checks (
                                application_id, user_id, old_status, new_status, raw_status,
                                confidence, evidence, check_result, checked_at, changed_at,
                                status_changed
                            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 0)
                            """,
                            (child_id, user_id, item.status.value, item.raw_status, item.confidence, item.evidence, CheckResult.SUCCESS.value, record.checked_at.isoformat(), record.checked_at.isoformat()),
                        )
                    else:
                        child_id = existing["id"]
                        previous_child = connection.execute(
                            "SELECT status, changed_at, applied_at, applied_at_evidence FROM applications WHERE id = ? AND user_id = ?",
                            (existing["id"], user_id),
                        ).fetchone()
                        child_old_status = ApplicationStatus(previous_child["status"])
                        child_changed = child_old_status is not item.status
                        child_changed_at = record.checked_at if child_changed else (self._parse_datetime(previous_child["changed_at"]) or record.checked_at)
                        connection.execute(
                            "INSERT INTO application_checks (application_id, user_id, old_status, new_status, raw_status, confidence, evidence, check_result, checked_at, changed_at, status_changed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                existing["id"],
                                user_id,
                                child_old_status.value,
                                item.status.value,
                                item.raw_status,
                                item.confidence,
                                item.evidence,
                                CheckResult.SUCCESS.value,
                                record.checked_at.isoformat(),
                                child_changed_at.isoformat(),
                                int(child_changed),
                            ),
                        )
                        connection.execute(
                            """
                            UPDATE applications
                            SET role = ?, role_confirmed = 1, status = ?,
                                stage = CASE WHEN stage_manual = 0 THEN ? ELSE stage END,
                                raw_status = ?, confidence = ?, evidence = ?, checked_at = ?,
                                applied_at = ?, applied_at_evidence = ?,
                                changed_at = ?, check_result = ?,
                                previous_status = CASE WHEN ? THEN ? ELSE previous_status END,
                                last_check_changed = ?, updated_at = ?
                            WHERE id = ? AND user_id = ?
                            """,
                            (
                                item.role,
                                item.status.value,
                                item.status.value,
                                item.raw_status,
                                item.confidence,
                                item.evidence,
                                record.checked_at.isoformat(),
                                item.applied_at or previous_child["applied_at"],
                                item.applied_at_evidence if item.applied_at else previous_child["applied_at_evidence"],
                                child_changed_at.isoformat(),
                                CheckResult.SUCCESS.value,
                                int(child_changed),
                                child_old_status.value,
                                int(child_changed),
                                now,
                                existing["id"],
                                user_id,
                            ),
                        )
                    if replacement_id is None:
                        replacement_id = child_id
                if updated["role"] == "待识别岗位" and not updated["role_confirmed"] and replacement_id is not None:
                    connection.execute("DELETE FROM applications WHERE id = ? AND user_id = ?", (application_id, user_id))
                    updated = connection.execute(
                        "SELECT * FROM applications WHERE id = ? AND user_id = ?",
                        (replacement_id, user_id),
                    ).fetchone()
        if updated is None:  # pragma: no cover - transaction invariant
            raise RuntimeError("Stored application disappeared after update")
        return self._application_from_row(updated)

    def list_checks(self, user_id: str, application_id: int) -> list[StoredCheck]:
        user_id = self._validated_user_id(user_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM application_checks
                WHERE user_id = ? AND application_id = ? ORDER BY id
                """,
                (user_id, application_id),
            ).fetchall()
        return [self._check_from_row(row) for row in rows]

    @staticmethod
    def _validated_user_id(user_id: str) -> str:
        normalized = user_id.strip()
        if not normalized:
            raise ValueError("user_id must not be blank")
        return normalized

    @classmethod
    def _application_from_row(cls, row: sqlite3.Row) -> StoredApplication:
        return StoredApplication(
            id=row["id"],
            company=row["company"],
            role=row["role"],
            url=row["url"],
            applied_at=date.fromisoformat(row["applied_at"]) if row["applied_at"] else None,
            applied_at_evidence=row["applied_at_evidence"],
            notes=row["notes"],
            status=ApplicationStatus(row["status"]),
            stage=row["stage"] or row["status"],
            stage_manual=bool(row["stage_manual"]),
            role_confirmed=bool(row["role_confirmed"]),
            raw_status=row["raw_status"],
            confidence=float(row["confidence"]),
            evidence=row["evidence"],
            checked_at=cls._parse_datetime(row["checked_at"]),
            changed_at=cls._parse_datetime(row["changed_at"]),
            check_result=CheckResult(row["check_result"]) if row["check_result"] else None,
            previous_status=(ApplicationStatus(row["previous_status"]) if row["previous_status"] else None),
            changed=bool(row["last_check_changed"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @classmethod
    def _check_from_row(cls, row: sqlite3.Row) -> StoredCheck:
        return StoredCheck(
            id=row["id"],
            application_id=row["application_id"],
            old_status=ApplicationStatus(row["old_status"]) if row["old_status"] else None,
            new_status=ApplicationStatus(row["new_status"]),
            raw_status=row["raw_status"],
            confidence=float(row["confidence"]),
            evidence=row["evidence"],
            check_result=CheckResult(row["check_result"]),
            checked_at=datetime.fromisoformat(row["checked_at"]),
            changed_at=datetime.fromisoformat(row["changed_at"]),
            status_changed=bool(row["status_changed"]),
        )

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None
