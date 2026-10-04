"""Reserved scheduler-only graph: no lead-agent or tools are assembled."""

import asyncio
import os

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.application_tracker.scheduling import run_scheduled_refresh, task_id_for
from deerflow.agents.thread_state import ThreadState, adapt_state_schema_for_mode


def enabled() -> bool:
    return os.getenv("JOBSCOUT_SCHEDULED_REFRESH_ENABLED", "") == "1"


def build_refresh_graph(*, execute=None, mode="full", snapshot_frequency=None):
    schema = adapt_state_schema_for_mode(ThreadState, mode, snapshot_frequency)

    async def refresh(state: ThreadState, config: RunnableConfig, runtime: Runtime):
        context = runtime.context if isinstance(runtime.context, dict) else {}
        owner = context.get("user_id")
        metadata = config.get("metadata", {})
        occurrence = metadata.get("scheduled_task_run_id")
        if not enabled() or context.get("is_internal") is not True or context.get("non_interactive") is not True or not isinstance(owner, str) or not owner:
            raise PermissionError("JobScout scheduled refresh requires trusted scheduler context")
        if metadata.get("scheduled_task_id") != task_id_for(owner) or not isinstance(occurrence, str) or not occurrence:
            raise PermissionError("JobScout scheduled occurrence is missing")
        if execute:
            result = await execute(owner, occurrence)
        else:
            from app.application_tracker.store import ApplicationTrackerStore

            tracker = await asyncio.to_thread(ApplicationTrackerStore)
            result = await run_scheduled_refresh(tracker, owner, occurrence)
        return {"messages": [AIMessage(f"投递定时检查完成：检查 {result['checked']} 条，未完成 {result['failed']} 条，跳过 {result['skipped']} 条。状态变化见站内通知。")]}

    builder = StateGraph(schema)
    builder.add_node("refresh", refresh, input_schema=schema)
    builder.add_edge(START, "refresh")
    builder.add_edge("refresh", END)
    return builder.compile()


def assemble_scheduled_refresh(config, *, app_config=None):
    from deerflow.config import get_app_config
    from deerflow.runtime.checkpoint_mode import INTERNAL_CHECKPOINT_MODE_KEY, freeze_checkpoint_channel_mode, freeze_checkpoint_snapshot_frequency, frozen_checkpoint_channel_mode, inject_checkpoint_mode

    app_config = app_config or (config.get("context") or {}).get("app_config") or get_app_config()
    requested = app_config.database.checkpoint_channel_mode
    if frozen_checkpoint_channel_mode() is not None:
        requested = (config.get("configurable") or {}).get(INTERNAL_CHECKPOINT_MODE_KEY, requested)
    mode = freeze_checkpoint_channel_mode(requested)
    frequency = freeze_checkpoint_snapshot_frequency(app_config.database.checkpoint_delta.snapshot_frequency)
    inject_checkpoint_mode(config, mode)
    return build_refresh_graph(mode=mode, snapshot_frequency=frequency)
