"""Per-invocation provider usage shared by research branches or tracker fallback.

Usage arrives after a call. One response (and already in-flight branches) can
exceed the limit; subsequent calls stop. This is not a provider billing cap.
"""

from dataclasses import replace
from threading import RLock

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage

STOP_NOTICE = "本次模型 Token 预算已达到上限或供应商未返回用量，已停止继续调用；当前结果可能不完整。"


class BudgetExceeded(RuntimeError):
    """No further model requests may start in this invocation."""


class RunTokenBudget:
    def __init__(self, config):
        self.config = config
        self.total = self.input = self.output = 0
        self.unknown_usage = False
        self._lock = RLock()

    @property
    def exhausted(self):
        with self._lock:
            return self.config.enabled and (
                self.unknown_usage
                or self.total >= self.config.max_tokens * self.config.hard_stop_threshold
                or (self.config.max_input_tokens is not None and self.input >= self.config.max_input_tokens)
                or (self.config.max_output_tokens is not None and self.output >= self.config.max_output_tokens)
            )

    def check(self):
        if self.exhausted:
            raise BudgetExceeded(STOP_NOTICE)

    def record(self, message):
        if not self.config.enabled:
            return
        usage = getattr(message, "usage_metadata", None)
        if not usage:
            raw = getattr(message, "response_metadata", {}).get("token_usage", {})
            usage = {"input_tokens": raw.get("prompt_tokens"), "output_tokens": raw.get("completion_tokens"), "total_tokens": raw.get("total_tokens")}
        with self._lock:
            values = [usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")]
            if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values):
                self.unknown_usage = True
                return
            self.input += values[0]
            self.output += values[1]
            self.total += max(values[2], values[0] + values[1])


class JobScoutBudgetMiddleware(AgentMiddleware):
    def __init__(self, budget):
        self.budget = budget

    def _stop(self, message=None):
        message = message or AIMessage(STOP_NOTICE)
        return message.model_copy(update={"content": STOP_NOTICE, "tool_calls": [], "invalid_tool_calls": [], "additional_kwargs": {"jobscout_budget_stop": True}})

    def _account(self, response):
        for message in response.result:
            if isinstance(message, AIMessage):
                self.budget.record(message)
        if self.budget.exhausted:
            return replace(response, result=[self._stop(message) if isinstance(message, AIMessage) else message for message in response.result], structured_response=None)
        return response

    def wrap_model_call(self, request, handler):
        if self.budget.exhausted:
            return ModelResponse(result=[self._stop()])
        return self._account(handler(request))

    async def awrap_model_call(self, request, handler):
        if self.budget.exhausted:
            return ModelResponse(result=[self._stop()])
        return self._account(await handler(request))


class BudgetedModel:
    """Keep raw usage when structured extraction discards the provider message."""

    def __init__(self, model, budget, *, structured=False):
        self.model, self.budget, self.structured = model, budget, structured

    def bind_tools(self, tools, **kwargs):
        return BudgetedModel(self.model.bind_tools(tools, **kwargs), self.budget)

    def with_structured_output(self, schema, **kwargs):
        return BudgetedModel(self.model.with_structured_output(schema, **{**kwargs, "include_raw": True}), self.budget, structured=True)

    def _account(self, result):
        self.budget.record(result.get("raw") if self.structured else result)
        if self.structured:
            if result.get("parsing_error"):
                raise result["parsing_error"]
            return result["parsed"]
        # Do not execute new planner actions after exhausting the allowance.
        if self.budget.exhausted and isinstance(result, AIMessage):
            raise BudgetExceeded(STOP_NOTICE)
        return result

    def invoke(self, *args, **kwargs):
        self.budget.check()
        return self._account(self.model.invoke(*args, **kwargs))

    async def ainvoke(self, *args, **kwargs):
        self.budget.check()
        return self._account(await self.model.ainvoke(*args, **kwargs))
