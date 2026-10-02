"""Opt-in configured middleware; imports flow app -> harness, never backwards."""

from __future__ import annotations

from dataclasses import replace

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from .links import collect_seen_urls, strip_unseen_links
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

    def __init__(self, *, registry: EvidenceRegistry | None = None):
        self.registry = registry if registry is not None else RUN_EVIDENCE

    def _enabled(self, runtime) -> bool:
        context = _context(runtime)
        return context.get("jobscout_mode") == "interview_prep" or self.registry.active(_key(runtime))

    def before_agent(self, state, runtime):
        context = _context(runtime)
        if context.get("jobscout_mode") == "interview_prep" and not context.get("is_subagent") and (key := _key(runtime)):
            self.registry.start(key)

    async def abefore_agent(self, state, runtime):
        self.before_agent(state, runtime)

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
            return self._filter(request, handler(request))
        except BaseException as error:
            self._on_error(request.runtime, error)
            raise

    async def awrap_model_call(self, request, handler):
        try:
            return self._filter(request, await handler(request))
        except BaseException as error:
            self._on_error(request.runtime, error)
            raise
