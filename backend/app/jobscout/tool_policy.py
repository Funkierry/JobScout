"""Allowlist at both model binding and execution, including research children."""

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage


class JobScoutToolPolicy(AgentMiddleware):
    def __init__(self, mode, *, child=False):
        self.allowed = frozenset({"read_file"} if mode == "base_match" else {"read_file", "web_search", "web_fetch"} if child else {"read_file", "web_search", "web_fetch", "task"})

    def filter_tools(self, tools):
        return [tool for tool in tools if tool.name in self.allowed]

    def wrap_model_call(self, request, handler):
        return handler(request.override(tools=self.filter_tools(request.tools)))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(tools=self.filter_tools(request.tools)))

    def _denied(self, request):
        call = request.tool_call
        if call.get("name") not in self.allowed:
            return ToolMessage(content="此工具不在当前 JobScout 模式的允许列表内。", tool_call_id=call["id"], status="error")
        return None

    def wrap_tool_call(self, request, handler):
        denied = self._denied(request)
        return denied if denied is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        denied = self._denied(request)
        return denied if denied is not None else await handler(request)
