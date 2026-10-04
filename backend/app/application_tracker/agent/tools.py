"""Stateful, narrowly scoped tools for one application-status run."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from langchain.tools import BaseTool, tool
from pydantic import ValidationError

from app.application_tracker.browser.live_session import AgentBrowser
from app.application_tracker.browser.models import BrowserAccessResult
from app.application_tracker.confidence import assess_status, role_scope
from app.application_tracker.dates import parse_grounded_applied_at
from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
    StatusExtraction,
    StatusRecord,
)
from app.application_tracker.observations import CapturedJsonResponse, SourceObservation, dom_observation
from app.evidence.grounding import ground_excerpt

_DESTRUCTIVE_CLICK_MARKERS = (
    "withdraw",
    "delete",
    "remove application",
    "cancel application",
    "submit application",
    "accept offer",
    "decline offer",
    "撤回",
    "删除",
    "取消申请",
    "提交申请",
    "接受offer",
    "拒绝offer",
    "立即投递",
    "投递简历",
    "提交投递",
)
_APPLICATION_LISTING_CLICK_MARKERS = (
    "我的投递",
    "投递记录",
    "投递进度",
    "我的申请",
    "申请记录",
    "应聘记录",
    "我的应聘",
)
_READ_ONLY_CLICK_MARKERS = (
    "view",
    "detail",
    "status",
    "progress",
    "application history",
    "interview schedule",
    "my applications",
    "查看",
    "详情",
    "状态",
    "进度",
    "流程",
    "面试安排",
    *_APPLICATION_LISTING_CLICK_MARKERS,
)


class ScreenshotObservation:
    """Screenshot bytes prepared for a vision-capable chat model."""

    def __init__(self, data: bytes, media_type: str = "image/png") -> None:
        self.data = data
        self.media_type = media_type

    @property
    def data_url(self) -> str:
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.media_type};base64,{encoded}"


class ApplicationTrackerToolbox:
    """Own mutable browser/run state while exposing only six model tools."""

    def __init__(
        self,
        *,
        application: ApplicationInput,
        browser: AgentBrowser,
        previous: StatusRecord | None = None,
        checked_at: datetime | None = None,
        max_page_chars: int = 50_000,
        max_screenshot_bytes: int = 5_000_000,
    ) -> None:
        if max_page_chars <= 0 or max_screenshot_bytes <= 0:
            raise ValueError("agent observation limits must be positive")
        self.application = application
        self.browser = browser
        self.previous = previous
        self.checked_at = checked_at or datetime.now(UTC)
        self.max_page_chars = max_page_chars
        self.max_screenshot_bytes = max_screenshot_bytes
        self.page_text = ""
        self.observations: tuple[SourceObservation, ...] = ()
        self.json_responses: tuple[CapturedJsonResponse, ...] = ()
        self.record: StatusRecord | None = None
        self.last_access: BrowserAccessResult | None = None
        self.screenshot_seen = False
        self.human_login_requested = False
        self._element_names: dict[int, str] = {}
        self.tools = self._build_tools()

    async def open_page(self) -> str:
        self.last_access = await self.browser.open_page()
        self.page_text = self.last_access.page_text[: self.max_page_chars]
        self.observations = self.last_access.observations
        self.json_responses = self.last_access.json_responses
        self.screenshot_seen = False
        return await self._page_payload(action="opened")

    async def get_page_text(self) -> str:
        self.page_text = (await self.browser.get_page_text())[: self.max_page_chars]
        await self._read_observations()
        return await self._page_payload(action="read")

    async def click(self, *, ref: int) -> str:
        name = self._element_names.get(ref)
        if name is None:
            raise ValueError("ref is missing or stale; refresh the page observation")
        normalized_name = "".join(name.casefold().split())
        is_destructive = any("".join(marker.casefold().split()) in normalized_name for marker in _DESTRUCTIVE_CLICK_MARKERS)
        is_read_only = any("".join(marker.casefold().split()) in normalized_name for marker in _READ_ONLY_CLICK_MARKERS)
        if not normalized_name or is_destructive or not is_read_only:
            raise ValueError("click blocked by the read-only application safety policy")
        await self.browser.click(ref)
        self.page_text = (await self.browser.get_page_text())[: self.max_page_chars]
        self.screenshot_seen = False
        await self._read_observations()
        return await self._page_payload(action=f"clicked_ref_{ref}")

    async def navigate_application_listing(self) -> bool:
        """Open a known read-only application tab when the landing page has no status."""
        for marker in _APPLICATION_LISTING_CLICK_MARKERS:
            normalized_marker = "".join(marker.casefold().split())
            for ref, name in self._element_names.items():
                normalized_name = "".join(name.casefold().split())
                if normalized_name.startswith(normalized_marker):
                    await self.click(ref=ref)
                    return True
        return False

    async def screenshot(self) -> ScreenshotObservation:
        data = await self.browser.screenshot()
        if len(data) > self.max_screenshot_bytes:
            raise ValueError("screenshot exceeds the configured byte limit")
        self.screenshot_seen = True
        return ScreenshotObservation(data)

    async def request_human_login(self) -> str:
        if self.human_login_requested:
            return self._error("human login can be requested only once per check")
        self.human_login_requested = True
        self.last_access = await self.browser.request_human_login()
        self.page_text = self.last_access.page_text[: self.max_page_chars]
        self.observations = self.last_access.observations
        self.json_responses = self.last_access.json_responses
        self.screenshot_seen = False
        return await self._page_payload(action="human_login_finished")

    async def update_record(
        self,
        *,
        status: ApplicationStatus,
        raw_status: str,
        confidence: float,
        evidence: str,
        detected_role: str = "",
        role_evidence: str = "",
        applied_at: str = "",
        applied_at_evidence: str = "",
    ) -> str:
        try:
            extraction = StatusExtraction(
                status=status,
                raw_status=raw_status,
                confidence=confidence,
                evidence=evidence,
                detected_role=detected_role,
                role_evidence=role_evidence,
                applied_at=applied_at,
                applied_at_evidence=applied_at_evidence,
            )
        except ValidationError as exc:
            return self._error(f"invalid status fields: {exc.errors()[0]['msg']}")

        sources = [*self.observations, dom_observation(self.page_text)]
        # Vision can guide navigation, but a screenshot alone cannot verify text.
        grounded = any(ground_excerpt(source.text, extraction.raw_status) is not None and ground_excerpt(source.text, extraction.evidence) is not None for source in sources)
        role_grounded = any(ground_excerpt(source.text, extraction.role_evidence) is not None and ground_excerpt(source.text, extraction.detected_role) is not None for source in sources)
        if not role_grounded:
            return self._error("role_evidence must be copied from the current page text")
        if not grounded:
            if not self.screenshot_seen:
                return self._error("raw_status and evidence must be copied from the current page text")
            extraction = extraction.model_copy(update={"status": ApplicationStatus.UNKNOWN, "raw_status": "", "evidence": "", "applied_at": "", "applied_at_evidence": ""})
        assessment = assess_status(extraction.status, raw_status=extraction.raw_status, evidence=extraction.evidence, role=extraction.detected_role or self.application.role, sources=sources)
        status, confidence = assessment.status, assessment.confidence
        parsed_applied_at, grounded_applied_at_evidence = parse_grounded_applied_at(
            extraction.applied_at,
            extraction.applied_at_evidence,
            page_text=assessment.source_text or role_scope(dom_observation(self.page_text), extraction.detected_role or self.application.role),
            checked_at=self.checked_at,
        )

        changed_at = self.checked_at
        if self.previous is not None and self.previous.status is status:
            changed_at = self.previous.changed_at
        self.record = StatusRecord(
            company=self.application.company,
            role=self.application.role,
            url=self.application.url,
            status=status,
            raw_status=extraction.raw_status,
            confidence=confidence,
            evidence=extraction.evidence,
            applied_at=parsed_applied_at,
            applied_at_evidence=grounded_applied_at_evidence,
            detected_role=extraction.detected_role if role_grounded else "",
            checked_at=self.checked_at,
            changed_at=changed_at,
            check_result=CheckResult.SUCCESS,
        )
        return json.dumps(
            {"updated": True, "record": self.record.model_dump(mode="json")},
            ensure_ascii=False,
        )

    async def _read_observations(self) -> None:
        observe = getattr(self.browser, "get_observations", None)
        self.observations = tuple(await observe()) if observe is not None else ()
        capture = getattr(self.browser, "get_json_responses", None)
        self.json_responses = tuple(await capture()) if capture is not None else ()

    def extraction_kwargs(self) -> dict[str, Any]:
        return {"observations": self.observations} if self.observations else {}

    def accept_fast_path_record(self, record: StatusRecord) -> None:
        self.record = record

    async def _page_payload(self, *, action: str) -> str:
        access = self.last_access
        if access is not None and access.check_result is not CheckResult.SUCCESS:
            self._element_names = {}
            return json.dumps(
                {
                    "action": action,
                    "check_result": access.check_result.value,
                    "login_state": access.login_state.value,
                    "error_code": access.error_code,
                },
                ensure_ascii=False,
            )
        elements = await self.browser.get_interactive_elements()
        self._element_names = {item.ref: item.name for item in elements}
        return json.dumps(
            {
                "action": action,
                "check_result": CheckResult.SUCCESS.value,
                "page_text": self.page_text,
                "untrusted_json_observations": [item.model_dump() for item in self.observations],
                "interactive_elements": [{"ref": item.ref, "role": item.role, "name": item.name} for item in elements],
            },
            ensure_ascii=False,
        )

    def _is_grounded(self, candidate: str) -> bool:
        if not candidate.strip():
            return True
        return candidate in self.page_text

    @staticmethod
    def _error(message: str) -> str:
        return json.dumps({"error": message})

    def _build_tools(self) -> list[BaseTool]:
        toolbox = self

        @tool("open_page")
        async def open_page_tool() -> str:
            """Open this run's registered application URL and inspect its login state."""
            return await toolbox.open_page()

        @tool("get_page_text")
        async def get_page_text_tool() -> str:
            """Read bounded visible text and clickable element references from the current page."""
            return await toolbox.get_page_text()

        @tool("screenshot")
        async def screenshot_tool() -> ScreenshotObservation:
            """Capture the current viewport when useful status text is not available in the DOM."""
            return await toolbox.screenshot()

        @tool("click")
        async def click_tool(ref: int) -> str:
            """Click a numbered element from the latest page observation, then return the new page."""
            return await toolbox.click(ref=ref)

        @tool("request_human_login")
        async def request_human_login_tool() -> str:
            """Open a visible browser for manual login, CAPTCHA, SMS, or risk-control handling."""
            return await toolbox.request_human_login()

        @tool("update_record")
        async def update_record_tool(
            status: ApplicationStatus,
            raw_status: str,
            confidence: float,
            evidence: str,
            detected_role: str = "",
            role_evidence: str = "",
            applied_at: str = "",
            applied_at_evidence: str = "",
        ) -> str:
            """Validate and accept the normalized status for this application."""
            return await toolbox.update_record(
                status=status,
                raw_status=raw_status,
                confidence=confidence,
                evidence=evidence,
                detected_role=detected_role,
                role_evidence=role_evidence,
                applied_at=applied_at,
                applied_at_evidence=applied_at_evidence,
            )

        return [
            open_page_tool,
            get_page_text_tool,
            screenshot_tool,
            click_tool,
            request_human_login_tool,
            update_record_tool,
        ]


def serialize_tool_error(exc: Exception) -> str:
    """Return an intentionally terse error without page or credential data."""
    return json.dumps({"error": type(exc).__name__})


def tool_by_name(tools: list[BaseTool]) -> dict[str, BaseTool]:
    return {item.name: item for item in tools}


async def invoke_bound_tool(tool: BaseTool, arguments: dict[str, Any]) -> Any:
    return await tool.ainvoke(arguments)
