"""Conservative rule scores, not model probabilities or ATS adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.application_tracker.models import ApplicationStatus
from app.application_tracker.observations import SourceObservation
from app.application_tracker.status_semantics import normalize_generic_active_status
from app.evidence.grounding import ground_excerpt

_VOCABULARY = {
    ApplicationStatus.APPLIED: r"已投递|投递成功|已提交|提交成功|已收到|申请成功|submitted|received|applied",
    ApplicationStatus.RESUME_SCREENING: r"筛选|简历审核|材料审核|审核中|待审核|进行中|处理中|待处理|推进中|screening|review|in\s*progress|processing",
    ApplicationStatus.ASSESSMENT: r"测评|测验|assessment",
    ApplicationStatus.WRITTEN_TEST: r"笔试|在线考试|written\s*test|coding\s*test",
    ApplicationStatus.FIRST_INTERVIEW: r"一面|第一轮面试|首轮面试|first\s*interview|interview\s*1",
    ApplicationStatus.SECOND_INTERVIEW: r"二面|第二轮面试|second\s*interview|interview\s*2",
    ApplicationStatus.THIRD_INTERVIEW: r"三面|第三轮面试|third\s*interview|interview\s*3",
    ApplicationStatus.HR_INTERVIEW: r"hr\s*面|人力.*面试|hr\s*interview",
    ApplicationStatus.OFFER: r"offer|已录用|录用通知|hired",
    ApplicationStatus.REJECTED: r"未通过|不通过|淘汰|拒绝|不再推进|rejected|unsuccessful",
    ApplicationStatus.TERMINATED: r"终止|撤回|取消|招聘关闭|withdrawn|cancelled|canceled|closed|terminated",
}


def status_signals(text: str) -> set[ApplicationStatus]:
    return {status for status, pattern in _VOCABULARY.items() if re.search(pattern, text, re.I)}


def role_scope(source: SourceObservation, role: str, roles: tuple[str, ...] = ()) -> str:
    """Cut a text listing at role boundaries; JSON objects are already separate."""
    text = source.text
    if source.kind == "json":
        return text
    # Tool updates do not carry a discovered list. Recover explicit DOM labels
    # independently, so omitting discovered_applications cannot bypass row checks.
    labeled = tuple(re.findall(r"(?:岗位|职位|role|position|job(?:title|name)?)\s*[:：]\s*([^\n:：]{1,300})", text, re.I))
    roles = tuple(set(roles + labeled))
    if not roles:
        return text
    positions = sorted((match.start(), name) for name in set(roles) if name for match in re.finditer(re.escape(name), text))
    matches = [(position, name) for position, name in positions if name == role]
    if len(matches) != 1:
        return ""
    start = text.rfind("\n", 0, matches[0][0]) + 1
    end = next((text.rfind("\n", start, position) + 1 or position for position, name in positions if position > matches[0][0] and name != role), len(text))
    # Two roles on the same line are ambiguous; do not merge their status.
    if end <= start:
        return ""
    return text[start:end]


@dataclass(frozen=True)
class StatusAssessment:
    status: ApplicationStatus
    confidence: float
    source_text: str = ""


def assess_status(status: ApplicationStatus, *, raw_status: str, evidence: str, role: str, sources: list[SourceObservation], roles: tuple[str, ...] = (), require_role: bool = False) -> StatusAssessment:
    status, cap = normalize_generic_active_status(status, raw_status=raw_status, evidence=evidence, confidence=1)
    if status is ApplicationStatus.UNKNOWN or not raw_status or not evidence:
        return StatusAssessment(ApplicationStatus.UNKNOWN, 0)
    signals = status_signals(raw_status)
    if signals and (status not in signals or len(signals) != 1):
        return StatusAssessment(ApplicationStatus.UNKNOWN, 0)
    matching: list[tuple[SourceObservation, str]] = []
    for source in sources:
        text = role_scope(source, role, roles)
        if (require_role or source.kind == "json") and (not role or role not in text):
            continue
        if source.kind == "dom" and text == source.text and len(status_signals(text)) > 1 and role not in evidence:
            # Unstructured multi-status pages need a role-bearing quote instead
            # of guessing which unlabeled paragraph belongs to this application.
            continue
        if ground_excerpt(text, raw_status) and ground_excerpt(text, evidence) and ground_excerpt(evidence, raw_status):
            matching.append((source, text))
    if not matching:
        return StatusAssessment(ApplicationStatus.UNKNOWN, 0)
    # Current-status contradictions need a new observation/manual review. Do not
    # choose a terminal state just because it occurs somewhere in a history.
    for source in sources:
        scoped = role_scope(source, role, roles)
        if role and role in scoped:
            current = re.findall(r'(?:当前(?:申请)?(?:状态|进度|阶段)|current\s*(?:status|stage)|"(?:status|stage)")\s*[:：]?\s*[^\n,}]{1,100}', scoped, re.I)
            other = set().union(*(status_signals(value) for value in current)) if current else set()
            if other and other != {status}:
                return StatusAssessment(ApplicationStatus.UNKNOWN, 0)
    source, text = matching[0]
    score = 0.4 + (0.35 if status in signals else 0) + (0.1 if role and role in text else 0) + (0.1 if source.kind == "json" else 0.05)
    return StatusAssessment(status, round(min(score, cap), 2), text)
