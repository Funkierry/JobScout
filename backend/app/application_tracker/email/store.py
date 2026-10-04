"""Private mail ledger and a derived view; portal history is never overwritten."""

import json
from datetime import UTC, datetime
from hashlib import sha256

from app.application_tracker.email.models import MailEvent, MailMessage
from app.evidence.grounding import is_evidence_grounded

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobscout_mail_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
 application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,
 fingerprint TEXT NOT NULL, event_json TEXT NOT NULL,
 received_at TEXT NOT NULL, UNIQUE(user_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_mail_owner_app ON jobscout_mail_events(user_id, application_id, received_at);
"""

SPECIFICITY = {"未知": 0, "已投递": 1, "简历筛选": 1, "测评": 2, "笔试": 2, "面试（轮次待确认）": 3, "一面": 4, "二面": 4, "三面": 4, "HR面": 4, "Offer": 5, "未通过": 5, "流程终止": 5}
TERMINAL = {"Offer", "未通过", "流程终止"}


class MailStore:
    def __init__(self, tracker):
        self.tracker = tracker

    def save(self, user_id: str, message: MailMessage, event: MailEvent) -> bool:
        user_id = self.tracker._validated_user_id(user_id)
        source = message.subject + "\n" + message.body
        if event.received_at != message.received_at or (event.quote and not is_evidence_grounded(source, event.quote)) or (event.time_quote and not is_evidence_grounded(source, event.time_quote)):
            raise ValueError("Mail evidence is not grounded")
        if event.status and not event.quote:
            raise ValueError("Known mail events need evidence")
        fingerprint = sha256(f"{message.provider}\n{message.message_id}".encode()).hexdigest()
        with self.tracker._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = None
            if event.application_id is not None:
                row = connection.execute("SELECT * FROM applications WHERE user_id=? AND id=?", (user_id, event.application_id)).fetchone()
                if row is None or row["company"] != event.company or row["role"] != event.role or not all(is_evidence_grounded(source, text) for text in (event.company, event.role)):
                    raise ValueError("Mail application ownership or evidence mismatch")
                app = self.tracker._application_from_row(row)
                previous = self._decorate([app], self._events(connection, user_id))[0]
            result = connection.execute(
                "INSERT OR IGNORE INTO jobscout_mail_events(user_id,application_id,fingerprint,event_json,received_at) VALUES(?,?,?,?,?)",
                (user_id, event.application_id, fingerprint, event.model_dump_json(), event.received_at.astimezone(UTC).isoformat()),
            )
            if result.rowcount and previous is not None:
                current = self._decorate([app], self._events(connection, user_id))[0]
                before = (previous.source_summary or {}).get("status", previous.status.value)
                after = current.source_summary or {}
                if before != "未知" and after.get("source") == "email" and not after.get("conflict") and after["status"] != before:
                    connection.execute(
                        "INSERT OR IGNORE INTO jobscout_notifications(user_id,application_id,old_status,new_status,evidence,source,source_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (user_id, app.id, before, after["status"], after["evidence"], "email", str(after["event_id"]), datetime.now(UTC).isoformat()),
                    )
        return result.rowcount > 0

    def list_events(self, user_id: str, application_id: int | None = None) -> list[dict]:
        user_id = self.tracker._validated_user_id(user_id)
        with self.tracker._connect() as connection:
            rows = connection.execute(
                "SELECT id,event_json FROM jobscout_mail_events WHERE user_id=?" + (" AND application_id=?" if application_id is not None else "") + " ORDER BY received_at DESC,id DESC LIMIT 200",
                (user_id, application_id) if application_id is not None else (user_id,),
            ).fetchall()
        return [{"id": row["id"], **json.loads(row["event_json"])} for row in rows]

    def decorate(self, user_id: str, applications: list):
        # One bounded query per user listing, not per application.
        with self.tracker._connect() as connection:
            events = self._events(connection, self.tracker._validated_user_id(user_id))
        return self._decorate(applications, events)

    @staticmethod
    def _events(connection, user_id):
        rows = connection.execute(
            """SELECT id,event_json FROM (
                SELECT id,event_json,DENSE_RANK() OVER (PARTITION BY application_id ORDER BY received_at DESC) AS n
                FROM jobscout_mail_events WHERE user_id=? AND application_id IS NOT NULL
                AND json_extract(event_json,'$.review_reason') IS NULL AND json_extract(event_json,'$.status') IS NOT NULL
            ) WHERE n <= 2 ORDER BY id DESC""",
            (user_id,),
        ).fetchall()
        return sorted([{"id": row["id"], **json.loads(row["event_json"])} for row in rows], key=lambda event: (datetime.fromisoformat(event["received_at"]), event["id"]), reverse=True)

    @staticmethod
    def _decorate(applications, events):
        results = []
        for app in applications:
            candidates = [event for event in events if event["application_id"] == app.id and event["status"] and not event["review_reason"]]
            if not candidates:
                results.append(app)
                continue
            latest = candidates[0]
            receipt = datetime.fromisoformat(latest["received_at"])
            portal_status = app.status.value
            status = latest["status"]
            # Receipt announces an event; appointment time never orders status updates.
            newer = app.checked_at is None or receipt > app.checked_at
            conflict = status != portal_status and (portal_status in TERMINAL or (portal_status != "未知" and (not newer or SPECIFICITY.get(status, 0) <= SPECIFICITY.get(portal_status, 0))))
            same_time_conflict = any(event["received_at"] == latest["received_at"] and event["status"] != status for event in candidates)
            mail_regression = any(event["status"] != status and (event["status"] in TERMINAL or SPECIFICITY.get(event["status"], 0) > SPECIFICITY.get(status, 0)) for event in candidates[1:])
            conflict = conflict or same_time_conflict or mail_regression
            selected = not conflict and (portal_status == "未知" or newer)
            summary = {
                "status": status if selected else portal_status,
                "source": "email" if selected else "portal",
                "conflict": conflict,
                "email_status": status,
                "evidence": latest["quote"],
                "received_at": latest["received_at"],
                "event_at": latest["event_at"],
                "time_evidence": latest["time_quote"],
                "event_id": latest["id"],
            }
            results.append(app.model_copy(update={"source_summary": summary}))
        return results
