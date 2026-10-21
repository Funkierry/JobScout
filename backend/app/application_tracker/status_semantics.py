"""Conservative normalization for broad, non-terminal application wording."""

from __future__ import annotations

import re

from app.application_tracker.models import ApplicationStatus

_GENERIC_ACTIVE_PHRASES = frozenset(
    {
        "进行中",
        "流程进行中",
        "申请进行中",
        "仍在进行中",
        "处理中",
        "申请处理中",
        "审核中",
        "待审核",
        "待处理",
        "推进中",
        "inprogress",
        "underreview",
        "processing",
    }
)
_APPLICATION_STATUS_CONTEXT = re.compile(
    r"(?:当前|申请|投递|应聘|岗位|职位|招聘).{0,12}(?:状态|进度|流程|环节)"
    r"|(?:申请|投递|应聘).{0,24}(?:进行中|处理中|审核中|待处理|推进中|筛选中)"
    r"|(?:application|candidate).{0,20}(?:status|progress)"
    r"|(?:status|progress).{0,20}(?:application|candidate)",
    re.IGNORECASE,
)
_MORE_SPECIFIC_OR_TERMINAL = re.compile(
    r"简历(?:审核|筛选|评估)|材料审核|筛选中|resume\s*review|screening"
    r"|面试|笔试|测评|考试|测验|录用|终止|未通过|淘汰|拒绝|撤回|取消|关闭"
    r"|interview|assessment|\btest\b|written|offer|rejected|withdrawn|closed|cancelled|terminated|hired",
    re.IGNORECASE,
)
_MAX_GENERIC_CONFIDENCE = 0.75


def normalize_generic_active_status(
    status: ApplicationStatus,
    *,
    raw_status: str,
    evidence: str,
    confidence: float,
) -> tuple[ApplicationStatus, float]:
    """Preserve generic progress without asserting a specific recruitment stage."""
    if status not in {ApplicationStatus.UNKNOWN, ApplicationStatus.RESUME_SCREENING, ApplicationStatus.IN_PROGRESS}:
        return status, confidence
    phrase = re.sub(r"[\s:：。.!！]+", "", raw_status).casefold()
    if phrase not in _GENERIC_ACTIVE_PHRASES:
        return status, confidence
    normalized_evidence = re.sub(r"[\s:：。.!！]+", "", evidence).casefold()
    if phrase not in normalized_evidence:
        return status, confidence
    if not _APPLICATION_STATUS_CONTEXT.search(evidence) or _MORE_SPECIFIC_OR_TERMINAL.search(evidence):
        return status, confidence
    return ApplicationStatus.IN_PROGRESS, min(confidence, _MAX_GENERIC_CONFIDENCE)


def migrate_generic_statuses(connection) -> None:
    """Reclassify legacy current rows only; preserve manual stages and history.

    This idempotent correction creates no checks or notifications. A revision
    increment fences any in-flight pre-migration refresh. Historical observations
    remain immutable, including their original classification and timestamps.
    """
    columns = {row[1] for row in connection.execute("PRAGMA table_info(applications)")}
    if not {"id", "status", "raw_status", "evidence", "confidence", "stage", "stage_manual", "revision"} <= columns:
        return  # Partial legacy schemas have no evidence to reclassify safely.
    rows = connection.execute("SELECT id,status,raw_status,evidence,confidence FROM applications WHERE status=?", (ApplicationStatus.RESUME_SCREENING.value,)).fetchall()
    for row in rows:
        status, score = normalize_generic_active_status(ApplicationStatus(row["status"]), raw_status=row["raw_status"], evidence=row["evidence"], confidence=row["confidence"])
        if status is ApplicationStatus.IN_PROGRESS:
            connection.execute(
                "UPDATE applications SET status=?, confidence=?, stage=CASE WHEN stage_manual=0 AND stage IN ('',?) THEN ? ELSE stage END, revision=revision+1 WHERE id=? AND status=?",
                (status.value, score, row["status"], status.value, row["id"], row["status"]),
            )
