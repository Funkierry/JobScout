"""User-isolated SQLite storage for applications and check history."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, computed_field

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
    notes: str
    status: ApplicationStatus
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

    @computed_field
    @property
    def terminal(self) -> bool:
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
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
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
                        SET company = ?, role = ?, applied_at = ?, notes = ?, updated_at = ?
                        WHERE id = ? AND user_id = ?
                        """,
                        (
                            application.company,
                            application.role,
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
        return [self._application_from_row(row) for row in rows]

    def get_application(self, user_id: str, application_id: int) -> StoredApplication | None:
        user_id = self._validated_user_id(user_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM applications WHERE user_id = ? AND id = ?",
                (user_id, application_id),
            ).fetchone()
        return None if row is None else self._application_from_row(row)

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
            status_changed = bool(successful and had_baseline and old_status != record.status)

            if successful:
                new_status = record.status
                raw_status = record.raw_status
                confidence = record.confidence
                evidence = record.evidence
                changed_at = record.checked_at if status_changed or not had_baseline else self._parse_datetime(row["changed_at"])
                if changed_at is None:
                    changed_at = record.checked_at
            else:
                new_status = ApplicationStatus(row["status"])
                raw_status = row["raw_status"]
                confidence = float(row["confidence"])
                evidence = row["evidence"]
                changed_at = self._parse_datetime(row["changed_at"]) or record.checked_at

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
                    record.raw_status,
                    record.confidence,
                    record.evidence,
                    record.check_result.value,
                    record.checked_at.isoformat(),
                    changed_at.isoformat(),
                    int(status_changed),
                ),
            )
            connection.execute(
                """
                UPDATE applications
                SET status = ?, raw_status = ?, confidence = ?, evidence = ?,
                    checked_at = ?, changed_at = ?, check_result = ?,
                    previous_status = ?, last_check_changed = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    new_status.value,
                    raw_status,
                    confidence,
                    evidence,
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
            notes=row["notes"],
            status=ApplicationStatus(row["status"]),
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
