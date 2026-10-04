from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    provider: Literal["gmail", "imap"] = "gmail"
    sender_domains: list[str] = Field(default_factory=list, max_length=30)
    keywords: list[str] = Field(default_factory=lambda: ["招聘", "面试", "笔试", "录用", "offer", "application", "interview", "assessment"], max_length=30)
    max_messages: int = Field(default=30, ge=1, le=100)
    lookback_days: int = Field(default=30, ge=1, le=90)
    timezone: str = "Asia/Shanghai"
    imap_host: str = ""
    imap_username: str = ""
    imap_password: str = Field(default="", repr=False, exclude=True)
    imap_readonly_acknowledged: bool = False
    access_token: str = Field(default="", repr=False, exclude=True)
    refresh_token: str = Field(default="", repr=False, exclude=True)
    client_id: str = Field(default="", repr=False, exclude=True)
    client_secret: str = Field(default="", repr=False, exclude=True)
    scope: str = ""

    @field_validator("sender_domains")
    @classmethod
    def domains(cls, values):
        import re

        if any(not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}", value) for value in values):
            raise ValueError("Use exact sender domains without wildcards")
        return sorted(set(value.lower() for value in values))

    @field_validator("keywords")
    @classmethod
    def safe_keywords(cls, values):
        if not values or any(not value.strip() or len(value) > 50 or any(c in value for c in '\r\n"\\') for value in values):
            raise ValueError("Invalid mail keywords")
        return values

    @field_validator("timezone")
    @classmethod
    def timezone_known(cls, value):
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
        return value

    def accepts(self, sender: str, subject: str) -> bool:
        address = parseaddr(sender)[1]
        domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
        return domain in self.sender_domains and any(keyword.casefold() in subject.casefold() for keyword in self.keywords)


class MailMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["gmail", "imap"]
    message_id: str = Field(min_length=1, max_length=300)
    sender: str = Field(max_length=500)
    subject: str = Field(max_length=1000)
    body: str = Field(max_length=256_000)
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Mail receipt must be timezone aware")
        return value.astimezone(UTC)


class MailEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: int | None = None
    company: str = ""
    role: str = ""
    kind: str = "unknown"
    status: str | None = None
    quote: str = Field(default="", max_length=1500)
    event_at: datetime | None = None
    time_quote: str = Field(default="", max_length=300)
    received_at: datetime
    review_reason: str | None = None
