"""Gateway-only activation and one-way binding of JobScout threads."""

import os

from fastapi import HTTPException
from langchain_core.messages import HumanMessage

BINDING_KEY = "jobscout_entry_version"


def enforce_thread_binding():
    """Strict binding is an operator opt-in on JobScout deployments only."""
    return os.environ.get("JOBSCOUT_ENFORCE_THREAD_BINDING", "") == "1"


def effective_assistant(record):
    if not isinstance(record, dict):
        return None
    if (record.get("metadata") or {}).get(BINDING_KEY) == 3:
        return "jobscout"
    return record.get("assistant_id")


def validate_input(graph_input):
    messages = graph_input.get("messages") if isinstance(graph_input, dict) else None
    if not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], HumanMessage):
        raise HTTPException(400, "JobScout 每轮只接受一条用户消息，不接受系统消息、模型消息或恢复命令。")
    message = messages[0]
    if "knowledge_scope" in message.additional_kwargs:
        raise HTTPException(400, "JobScout 不支持通用知识库模式。")
    # Only a fresh genuine user message crosses the gate. Prevent replacing an
    # older checkpoint message by choosing its id or hiding the current query.
    return {"messages": [HumanMessage(content=message.content, additional_kwargs={key: value for key, value in message.additional_kwargs.items() if key in {"files", "jobscout_mode"}})]}
