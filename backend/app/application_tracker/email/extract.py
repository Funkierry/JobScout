"""Bounded MIME parsing and conservative, grounded event templates; no LLM."""

import re
from datetime import datetime
from email import policy
from email.parser import BytesParser
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.application_tracker.email.models import MailEvent, MailMessage

EVENTS = {
    "written_test": ("笔试", r"邀请您参加笔试|笔试通知|笔试邀请|written test invitation"),
    "interview": ("面试（轮次待确认）", r"面试邀请|邀请您参加面试|interview invitation"),
    "offer": ("Offer", r"录用通知|正式\s*offer|offer\s*(?:letter|通知)|已被录用"),
    "rejection": ("未通过", r"未通过(?:本次)?(?:笔试|面试|筛选|招聘)|不予录用|很遗憾[^\n。]{0,50}(?:未通过|无法推进)|application.{0,30}unsuccessful"),
}


def current_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if re.match(r"\s*(?:-{2,}\s*(?:Original|原始)|On .+wrote:|发件人[:：])", line, re.I):
            break
        if not line.lstrip().startswith(">"):
            lines.append(line)
    return "\n".join(lines).strip()


def parse_mail(raw: bytes, *, provider: str, message_id: str, received_at: datetime) -> MailMessage:
    if len(raw) > 1_000_000:
        raise ValueError("Mail exceeds byte limit")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    part = message.get_body(preferencelist=("plain", "html"))
    text = ""
    if part is not None and part.get_content_disposition() != "attachment":
        text = part.get_content()
        if part.get_content_type() == "text/html":
            soup = BeautifulSoup(text, "html.parser")
            for element in soup.select("script,style,iframe,img,blockquote,.gmail_quote"):
                element.decompose()
            text = soup.get_text("\n")
    return MailMessage(provider=provider, message_id=message_id, sender=str(message.get("From", "")), subject=str(message.get("Subject", "")), body=current_text(text), received_at=received_at)


def extract_event(message: MailMessage, applications, *, timezone: str = "Asia/Shanghai") -> MailEvent:
    body = current_text(message.body)
    source = message.subject + "\n" + body
    matched = [app for app in applications if app.role_confirmed and app.company in source and app.role in source]
    base = {"received_at": message.received_at}
    if len(matched) != 1:
        return MailEvent(**base, review_reason="ambiguous_application" if matched else "application_not_found")
    app = matched[0]
    base.update(application_id=app.id, company=app.company, role=app.role)
    hits = []
    for kind, (status, pattern) in EVENTS.items():
        matches = list(re.finditer(pattern, body, re.I))
        for match in matches:
            prefix = body[max(0, match.start() - 12) : match.start()]
            if re.search(r"(?:不代表|并非|不是|尚未|未发放|非正式|示例|模板|样例|取消|作废|撤销|无需|不再)[^\n。]{0,6}$", prefix):
                continue
            hits.append((kind, status, match))
    if len({hit[0] for hit in hits}) != 1:
        return MailEvent(**base, review_reason="ambiguous_event" if hits else "event_not_found")
    kind, status, match = hits[0]
    event_at, time_quote = None, ""
    times = list(re.finditer(r"(?:笔试时间|面试时间|时间|日期)[:：]\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?!\d)", body))
    if len(times) == 1:
        try:
            event_at = datetime.fromisoformat(f"{times[0][1]}T{times[0][2]}").replace(tzinfo=ZoneInfo(timezone))
            time_quote = times[0][0]
        except ValueError:
            pass
    return MailEvent(**base, kind=kind, status=status, quote=match[0], event_at=event_at, time_quote=time_quote)
