"""Typed contracts for application status extraction and offline fixtures."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApplicationStatus(StrEnum):
    APPLIED = "已投递"
    RESUME_SCREENING = "简历筛选"
    ASSESSMENT = "测评"
    WRITTEN_TEST = "笔试"
    FIRST_INTERVIEW = "一面"
    SECOND_INTERVIEW = "二面"
    THIRD_INTERVIEW = "三面"
    HR_INTERVIEW = "HR面"
    OFFER = "Offer"
    REJECTED = "未通过"
    TERMINATED = "流程终止"
    UNKNOWN = "未知"


class CheckResult(StrEnum):
    SUCCESS = "成功"
    LOGIN_REQUIRED = "需登录"
    FETCH_FAILED = "抓取失败"


class ApplicationInput(BaseModel):
    """One row from the user's application source table."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2048)
    applied_at: date | None = None
    notes: str = Field(default="", max_length=5000)

    @field_validator("url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be an absolute HTTP or HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("url must not contain embedded credentials")
        return value


class OfflineExtractionCase(ApplicationInput):
    """Application metadata plus an already-captured page text snapshot."""

    case_id: str = Field(min_length=1, max_length=200)
    page_text: str = Field(max_length=500_000)


class StatusExtraction(BaseModel):
    """The only fields the LLM is allowed to decide."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: ApplicationStatus = Field(description="统一后的当前申请状态")
    raw_status: str = Field(default="", max_length=500, description="页面中的原始状态短语，必须逐字引用")
    confidence: float = Field(ge=0, le=1, description="当前状态判断的置信度，范围 0 到 1")
    evidence: str = Field(default="", max_length=1500, description="支持判断的页面原文片段，必须逐字引用")

    @model_validator(mode="after")
    def require_grounding_fields_for_known_status(self) -> StatusExtraction:
        if self.status is not ApplicationStatus.UNKNOWN:
            if not self.raw_status:
                raise ValueError("raw_status is required when status is not 未知")
            if not self.evidence:
                raise ValueError("evidence is required when status is not 未知")
        return self


class StatusRecord(BaseModel):
    """Public normalized output for one application check."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2048)
    status: ApplicationStatus
    raw_status: str = Field(default="", max_length=500)
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(default="", max_length=1500)
    checked_at: datetime
    changed_at: datetime
    check_result: CheckResult

    @field_validator("url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        return ApplicationInput.validate_http_url(value)

    @field_validator("checked_at", "changed_at")
    @classmethod
    def require_timezone_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def changed_at_cannot_follow_check(self) -> StatusRecord:
        if self.changed_at > self.checked_at:
            raise ValueError("changed_at must not be later than checked_at")
        return self
