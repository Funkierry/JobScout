"""Opt-in configured middleware; imports flow app -> harness, never backwards."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from deerflow.subagents.status_contract import SUBAGENT_STATUS_VALUES, make_subagent_additional_kwargs

from .evidence import FENCE, ResearchEvidence, parse_payload
from .inputs import BASE_SNAPSHOTS, read_uploaded_resumes
from .links import _text, collect_seen_urls, strip_unseen_links
from .matching import score_matches
from .protocol import instruction
from .render import cell, render_matches, render_prep
from .run_evidence import RUN_EVIDENCE, EvidenceRegistry


def _context(runtime) -> dict:
    value = getattr(runtime, "context", None)
    return value if isinstance(value, dict) else {}


def _key(runtime):
    context = _context(runtime)
    values = tuple(context.get(field) for field in ("user_id", "thread_id", "run_id"))
    return values if all(isinstance(value, str) and value for value in values) else None


class JobScoutLinkMiddleware(AgentMiddleware):
    """Collect completed research tools and filter model text before state writes.

    JobScout consumes values streams. Provider token callbacks/journal drafts
    precede this hook and are NOT verified final responses. Do not expose them as
    reports. Missing run identity or lost registry yields an empty allowlist.
    """

    def __init__(self, *, registry: EvidenceRegistry | None = None, trusted_resumes: dict | None = None, trusted_anchor: dict | None = None):
        self.registry = registry if registry is not None else RUN_EVIDENCE
        self.trusted_resumes = trusted_resumes
        self.trusted_anchor = trusted_anchor

    def _enabled(self, runtime) -> bool:
        context = _context(runtime)
        return self._v2(runtime) or context.get("jobscout_mode") == "interview_prep" or self.registry.active(_key(runtime))

    def _v2(self, runtime):
        context = _context(runtime)
        return self.registry.session(_key(runtime)) is not None or (context.get("jobscout_evidence_version") == 2 and context.get("jobscout_mode") in {"interview_prep", "base_match"})

    def before_agent(self, state, runtime):
        context = _context(runtime)
        if not context.get("is_subagent") and (key := _key(runtime)):
            if self._v2(runtime):
                snapshot = BASE_SNAPSHOTS.get_snapshot(context.get("jobscout_base_context_ref"), key[0], key[1]) or {}
                resumes = self.trusted_resumes if self.trusted_resumes is not None else read_uploaded_resumes(state, key[0], key[1])
                self.registry.start(key, ResearchEvidence(mode=context["jobscout_mode"], records=snapshot.get("records"), resumes=resumes, base_bounds=snapshot))
            elif context.get("jobscout_mode") == "interview_prep":
                self.registry.start(key)

    async def abefore_agent(self, state, runtime):
        # Owner upload snapshots involve disk I/O; do not block the event loop.
        await asyncio.to_thread(self.before_agent, state, runtime)

    def after_agent(self, state, runtime):
        if not _context(runtime).get("is_subagent"):
            self.registry.finish(_key(runtime))

    async def aafter_agent(self, state, runtime):
        self.after_agent(state, runtime)

    def _on_error(self, runtime, error: BaseException):
        # Outer DeerFlow middleware may recover/retry ordinary model or tool
        # errors. Do not erase successful sibling evidence on a failed fetch.
        # Cancellation/system exit is terminal; other aborted runs expire.
        if not isinstance(error, Exception):
            self.after_agent({}, runtime)

    def _observe(self, request, result):
        session = self.registry.session(_key(request.runtime))
        if session is None and self._v2(request.runtime):
            # Eviction must also fail closed before the lead sees task output.
            session = ResearchEvidence(mode=_context(request.runtime).get("jobscout_mode", "interview_prep"))
        if session is not None:
            if isinstance(result, Command) and request.tool_call.get("name") == "task":
                update = result.update
                if isinstance(update, dict) and isinstance(update.get("messages"), list):
                    return replace(result, update={**update, "messages": [self._task_message(message, session) for message in update["messages"]]})
            elif isinstance(result, ToolMessage):
                if request.tool_call.get("name") == "task":
                    return self._task_message(result, session)
                session.observe(request.tool_call, result.content, result.status)
                if request.tool_call.get("name") == "read_file":
                    session.observe_resume(request.tool_call, _text(result.content), result.status)
        if self._enabled(request.runtime) and isinstance(result, ToolMessage):
            call = request.tool_call
            if call.get("name") in {"web_search", "web_fetch"}:
                # Only actual handler results enter the registry; state/history is
                # deliberately ignored, including user-supplied metadata.
                urls = collect_seen_urls(
                    [
                        {"type": "ai", "tool_calls": [call]},
                        {"type": "tool", "name": call["name"], "tool_call_id": call["id"], "status": result.status, "content": result.content},
                    ]
                )
                self.registry.add(_key(request.runtime), urls)
        return result

    def _task_message(self, message, session):
        if not isinstance(message, ToolMessage):
            return message
        # Task transports contain model-visible wrappers and a duplicate raw
        # result in metadata. Neither may bypass the structured validator.
        failed = message.status != "success" or message.additional_kwargs.get("subagent_status") in {"failed", "cancelled", "timed_out", "polling_timed_out"}
        items = session.validate([] if failed else parse_payload(_text(message.content), "jobscout_evidence"))
        content = self._child_report(items)
        metadata = {"jobscout_evidence": {"version": 2, "accepted_count": len(items)}}
        status = message.additional_kwargs.get("subagent_status")
        if isinstance(status, str) and status in SUBAGENT_STATUS_VALUES:
            metadata.update(
                make_subagent_additional_kwargs(
                    status,
                    result=content,
                    stop_reason=message.additional_kwargs.get("subagent_stop_reason"),
                    model_name=message.additional_kwargs.get("subagent_model_name"),
                    token_usage=message.additional_kwargs.get("subagent_token_usage"),
                    tool_receipts=message.additional_kwargs.get("subagent_tool_receipts"),
                    receipt_verdict=message.additional_kwargs.get("subagent_receipt_verdict"),
                    acceptance_verdict=message.additional_kwargs.get("subagent_acceptance_verdict"),
                )
            )
        return message.model_copy(update={"content": content, "additional_kwargs": metadata, "artifact": None})

    @staticmethod
    def _child_report(items):
        lines = ["## 已校验研究证据", ""]
        lines.extend("- " + cell(item["claim"], 2000) for item in items)
        lines.extend(["", FENCE + "jobscout_evidence", json.dumps(items, ensure_ascii=False), FENCE])
        return "\n".join(lines)

    def _prepare(self, request):
        if not self._v2(request.runtime):
            return request
        session = self.registry.session(_key(request.runtime))
        session = session or ResearchEvidence(mode=_context(request.runtime).get("jobscout_mode", "interview_prep"))
        extra = instruction(session.mode, bool(_context(request.runtime).get("is_subagent")), session.as_of)
        original = getattr(request, "system_message", None)
        content = list(original.content) if original and isinstance(original.content, list) else ([{"type": "text", "text": original.content}] if original else [])
        content.append({"type": "text", "text": extra})
        return request.override(system_message=SystemMessage(content=content))

    def _filter_v2(self, request, response):
        context = _context(request.runtime)
        session = self.registry.session(_key(request.runtime))
        session = session or ResearchEvidence(mode=context.get("jobscout_mode", "interview_prep"))
        messages = []
        for message in response.result:
            if not isinstance(message, AIMessage):
                messages.append(message)
                continue
            if message.tool_calls:
                # A draft accompanying a tool call isn't a verified report.
                messages.append(message.model_copy(update={"content": ""}))
                continue
            raw = _text(message.content)
            if context.get("is_subagent"):
                items = session.validate(parse_payload(raw, "jobscout_evidence"))
                # JSON quotes are source data, not user-facing links. Altering
                # them here would destroy grounding on the parent-side check.
                cleaned, removed = self._child_report(items), 0
                counts = {"accepted_count": len(items)}
            elif session.mode == "base_match":
                payload = parse_payload(raw, "jobscout_match")
                matches = score_matches(payload.get("candidates") if isinstance(payload, dict) else None, session.records, session.read_resumes)
                cleaned, removed = strip_unseen_links(render_matches(matches, session), set())
                counts = {"accepted_count": len(matches), "zeroed_items": sum(bool(item["reason"]) for row in matches for item in row["score_items"])}
            else:
                payload = parse_payload(raw, "jobscout_report")
                if self.trusted_anchor is not None:
                    payload = {**(payload if isinstance(payload, dict) else {}), **{key: self.trusted_anchor.get(key, "") for key in ("company", "role", "recruitment_type")}}
                labels = {"company": "公司", "role": "岗位方向", "recruitment_type": "招聘类型"}
                missing = payload.get("missing_fields") if self.trusted_anchor is None and isinstance(payload, dict) and payload.get("kind") == "clarification" else None
                if isinstance(missing, list) and missing and all(isinstance(field, str) and field in labels for field in missing):
                    cleaned, removed = "请补充：" + "、".join(dict.fromkeys(labels[field] for field in missing)) + "。", 0
                else:
                    session.validate(payload.get("evidence", []) if isinstance(payload, dict) else None)
                    cleaned, removed = strip_unseen_links(render_prep(payload, list(session.items), session), session.urls)
                counts = {"accepted_count": len(session.items)}
            metadata = {"version": 2, "mode": session.mode, "run_id": context.get("run_id"), **counts, "rejected_counts": dict(session.rejected), "removed_count": removed, "trace_submitted": False}
            journal = context.get("__run_journal")
            if journal is not None:
                try:
                    journal.record_middleware(tag="jobscout_evidence", name=self.name, hook="wrap_model_call", action="validate_and_render", changes=metadata.copy())
                    metadata["trace_submitted"] = True
                except Exception:
                    pass
            messages.append(message.model_copy(update={"content": cleaned, "additional_kwargs": {"jobscout_evidence": metadata}}))
        return replace(response, result=messages, structured_response=None)

    def wrap_tool_call(self, request, handler):
        try:
            return self._observe(request, handler(request))
        except BaseException as error:
            self._on_error(request.runtime, error)
            raise

    async def awrap_tool_call(self, request, handler):
        try:
            return self._observe(request, await handler(request))
        except BaseException as error:
            self._on_error(request.runtime, error)
            raise

    def _filter(self, request, response):
        if self._v2(request.runtime):
            return self._filter_v2(request, response)
        if not self._enabled(request.runtime) or _context(request.runtime).get("is_subagent"):
            return response
        context = _context(request.runtime)
        seen = self.registry.urls(_key(request.runtime))
        messages = []
        for message in response.result:
            if not isinstance(message, AIMessage):
                messages.append(message)
                continue
            # Collapse visible text blocks, dropping provider citation/resource
            # blocks which would otherwise bypass Markdown destination checks.
            content = message.content
            if isinstance(content, list):
                content = "\n".join(part if isinstance(part, str) else part.get("text", "") for part in content if isinstance(part, str) or isinstance(part, dict) and part.get("type") == "text")
            cleaned, removed_count = strip_unseen_links(content, seen)
            metadata = {"version": 1, "mode": "interview_prep", "run_id": context.get("run_id"), "removed_count": removed_count, "seen_url_count": len(seen)}
            journal = context.get("__run_journal")
            metadata["trace_submitted"] = False
            if journal is not None:
                try:
                    journal.record_middleware(tag="jobscout_links", name=self.name, hook="wrap_model_call", action="strip_unseen_links", changes={**metadata, "message_id": message.id})
                    metadata["trace_submitted"] = True
                except Exception:
                    # The final checkpoint still carries counts; never fall back
                    # to raw output on audit storage failure.
                    pass
            messages.append(message.model_copy(update={"content": cleaned, "additional_kwargs": {**message.additional_kwargs, "jobscout_links": metadata}}))
        return replace(response, result=messages)

    def wrap_model_call(self, request, handler):
        try:
            return self._filter(request, handler(self._prepare(request)))
        except BaseException as error:
            self._on_error(request.runtime, error)
            raise

    async def awrap_model_call(self, request, handler):
        try:
            return self._filter(request, await handler(self._prepare(request)))
        except BaseException as error:
            self._on_error(request.runtime, error)
            raise
