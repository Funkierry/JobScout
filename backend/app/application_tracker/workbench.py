"""Private user work notes, independent of observed recruitment facts."""

import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .email.store import TERMINAL, MailStore
from .pagination import SummaryRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobscout_prep_plans (
 user_id TEXT NOT NULL, thread_id TEXT NOT NULL, report_key TEXT NOT NULL,
 revision INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(user_id,thread_id,report_key));
CREATE TABLE IF NOT EXISTS jobscout_feedback (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, request_id TEXT NOT NULL,
 thread_id TEXT, application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,
 payload TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(user_id,request_id));
CREATE INDEX IF NOT EXISTS idx_jobscout_feedback_owner ON jobscout_feedback(user_id,id);
"""


class WriteConflict(ValueError):
    """A retry changed its payload or another tab saved first."""


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=0)
    done: list[Literal["requirements", "experience", "practice"]] = Field(default_factory=list, max_length=3)
    experience: str = Field(default="", max_length=3000)
    materials: str = Field(default="", max_length=3000)

    @model_validator(mode="after")
    def canonical_done(self):
        self.done = sorted(set(self.done))
        return self


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9-]+$")
    category: Literal["correction", "report", "recommendation", "trial"]
    error_type: Literal["status", "identity", "evidence", "matching", "usability", "other"] = "other"
    thread_id: str | None = Field(default=None, min_length=1, max_length=200)
    application_id: int | None = Field(default=None, ge=1)
    report_key: str = Field(default="", pattern=r"^([a-f0-9]{64})?$")
    original: str = Field(default="", max_length=1500)
    correction: str = Field(default="", max_length=1500)
    reason: str = Field(min_length=1, max_length=1500)
    next_check: str = Field(default="", max_length=1500)
    outcome: Literal["accepted", "rejected", "completed", "blocked", "unspecified"] = "unspecified"
    followup_of: int | None = Field(default=None, ge=1, le=2**63 - 1)
    verification: Literal["not_checked", "passed", "failed"] = "not_checked"

    @model_validator(mode="after")
    def single_context(self):
        if self.thread_id and self.application_id:
            raise ValueError("Feedback has one resource context")
        if self.report_key and not self.thread_id:
            raise ValueError("Report feedback requires a thread")
        if self.verification != "not_checked" and (not self.followup_of or not self.correction):
            raise ValueError("Verification requires an original feedback record and actual result")
        return self


def pending_application(row, *, changed=False):
    source = row.get("source_summary") or {}
    mail = source.get("source") == "email" and not source.get("conflict")
    status = source.get("status") if mail else row["status"]
    stage = row["stage"] if row.get("stage_manual") else status
    if source.get("conflict"):
        kind, title, hint = "conflict", "核对来源冲突", "官网与招聘邮件不一致，请查看双方原文后确认。"
    elif status in TERMINAL:
        if not changed:
            return None
        kind, title, hint = "change", "查看新的进度变化", "有未读的已确认变化，请核对通知依据。"
    elif row.get("check_result") == "需登录":
        kind, title, hint = "login", "恢复招聘账号登录", "打开识别详情，按处理建议登录后重新检查。"
    elif changed:
        kind, title, hint = "change", "查看新的进度变化", "有未读的已确认变化，请核对通知依据。"
    elif stage in {"测评", "笔试", "一面", "二面", "三面", "HR面", "面试（轮次待确认）"} and (row.get("stage_manual") or mail or (row.get("checked_at") and row.get("confidence", 0) >= 0.7 and row.get("check_result") != "抓取失败")):
        kind, title, hint = "interview", "准备下一轮", f"当前阶段：{stage}。先核对具体安排，再整理相关经历。"
    elif status in {"未知", "进行中·具体阶段未知"} or row.get("check_result") == "抓取失败" or not row.get("checked_at") or (not mail and row.get("confidence", 0) < 0.7):
        kind, title, hint = "evidence", "补充或核对状态依据", "信息尚不足；查看原文和查询链接，不推定招聘阶段。"
    else:
        return None
    return dict(kind=kind, title=title, hint=hint, application_id=row["id"], company=row["company"], role=row["role"])


class WorkbenchStore:
    def __init__(self, tracker):
        self.tracker = tracker

    def plan(self, user_id, thread_id, report_key):
        with self.tracker._connect() as connection:
            row = connection.execute("SELECT * FROM jobscout_prep_plans WHERE user_id=? AND thread_id=? AND report_key=?", (self.tracker._validated_user_id(user_id), thread_id, report_key)).fetchone()
        return {**json.loads(row["payload"]), "revision": row["revision"]} if row else PlanInput(revision=0).model_dump()

    def save_plan(self, user_id, thread_id, report_key, body: PlanInput):
        user_id = self.tracker._validated_user_id(user_id)
        payload = json.dumps(body.model_dump(exclude={"revision"}), ensure_ascii=False, sort_keys=True)
        with self.tracker._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT revision,payload FROM jobscout_prep_plans WHERE user_id=? AND thread_id=? AND report_key=?", (user_id, thread_id, report_key)).fetchone()
            revision = row["revision"] if row else 0
            if row and row["payload"] == payload:
                return {**json.loads(payload), "revision": revision}
            if revision != body.revision:
                raise WriteConflict("准备笔记已在其他页面更新，请重新读取后再保存。")
            revision += 1
            connection.execute(
                "INSERT INTO jobscout_prep_plans VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,thread_id,report_key) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at",
                (user_id, thread_id, report_key, revision, payload, datetime.now(UTC).isoformat()),
            )
        return {**json.loads(payload), "revision": revision}

    def feedback(self, user_id, *, limit=50, before=None):
        with self.tracker._connect() as connection:
            query = "SELECT * FROM jobscout_feedback WHERE user_id=?"
            params = [self.tracker._validated_user_id(user_id)]
            if before is not None:
                query += " AND id<?"
                params.append(before)
            rows = connection.execute(query + " ORDER BY id DESC LIMIT ?", [*params, limit]).fetchall()
        return [{**json.loads(row["payload"]), "id": row["id"], "created_at": row["created_at"]} for row in rows]

    def add_feedback(self, user_id, body: FeedbackInput):
        user_id = self.tracker._validated_user_id(user_id)
        payload = json.dumps(body.model_dump(), ensure_ascii=False, sort_keys=True)
        with self.tracker._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if body.application_id and not connection.execute("SELECT 1 FROM applications WHERE user_id=? AND id=?", (user_id, body.application_id)).fetchone():
                raise ValueError("Application was not found")
            if body.followup_of:
                parent = connection.execute("SELECT payload FROM jobscout_feedback WHERE user_id=? AND id=?", (user_id, body.followup_of)).fetchone()
                if not parent:
                    raise ValueError("Feedback was not found")
                original = FeedbackInput.model_validate_json(parent["payload"])
                if (body.thread_id, body.application_id, body.report_key) != (original.thread_id, original.application_id, original.report_key):
                    raise ValueError("Feedback context does not match")
            row = connection.execute("SELECT * FROM jobscout_feedback WHERE user_id=? AND request_id=?", (user_id, body.request_id)).fetchone()
            if row:
                # Older records omit newer optional fields. Compare canonical
                # inputs so an upgrade does not break a lost-response retry.
                if FeedbackInput.model_validate_json(row["payload"]) != body:
                    raise WriteConflict("同一次反馈已保存其他内容，请新建反馈。")
                return {**json.loads(row["payload"]), "id": row["id"], "created_at": row["created_at"]}
            stamp = datetime.now(UTC).isoformat()
            result = connection.execute("INSERT INTO jobscout_feedback(user_id,request_id,thread_id,application_id,payload,created_at) VALUES(?,?,?,?,?,?)", (user_id, body.request_id, body.thread_id, body.application_id, payload, stamp))
            return {**body.model_dump(), "id": result.lastrowid, "created_at": stamp}

    def pending(self, user_id, *, limit=20, offset=0):
        user_id = self.tracker._validated_user_id(user_id)
        with self.tracker._connect() as connection:
            connection.execute("BEGIN")
            rows = connection.execute("SELECT id,company,role,status,stage,stage_manual,confidence,checked_at,check_result FROM applications WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
            projected = MailStore._decorate([SummaryRow.model_validate(dict(row)) for row in rows], MailStore._events(connection, user_id, compact=True))
            changed = {row[0] for row in connection.execute("SELECT DISTINCT application_id FROM jobscout_notifications WHERE user_id=? AND is_read=0", (user_id,))}
            items = []
            for raw, resolved in zip(rows, projected, strict=True):
                item = pending_application({**dict(raw), "source_summary": resolved.source_summary}, changed=raw["id"] in changed)
                if item:
                    items.append(item)
            targets = connection.execute("SELECT id,company,role FROM jobscout_opportunities WHERE user_id=? AND trim(jd_text)='' ORDER BY id DESC", (user_id,)).fetchall()
            items.extend(dict(kind="materials", title="补充岗位 JD", hint="目标岗位尚未保存 JD；在准备对话中补充岗位原文。", opportunity_id=row["id"], company=row["company"], role=row["role"]) for row in targets)
        order = {"conflict": 0, "login": 1, "change": 2, "interview": 3, "evidence": 4, "materials": 5}
        items.sort(key=lambda item: order[item["kind"]])
        return {"items": items[offset : offset + limit], "total": len(items), "has_more": offset + limit < len(items)}
