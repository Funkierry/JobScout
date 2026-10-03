"""Gate first; assemble the main agent and tools only after admission."""

import json
from dataclasses import asdict
from functools import partial

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import adapt_state_schema_for_mode
from deerflow.utils.assembly_io import run_assembly

from .config import classify_with_model
from .inputs import BASE_SNAPSHOTS, read_uploaded_resumes
from .intent import Decision, decide, fixed_reply
from .links import _text
from .state import JobScoutState


def _latest_user(state):
    return next((message for message in reversed(state.get("messages", [])) if isinstance(message, HumanMessage)), HumanMessage(""))


def _context(runtime):
    return dict(runtime.context) if isinstance(runtime.context, dict) else {}


def build_entry_graph(*, agent_factory=None, fallback=None, app_config=None, mode="full", snapshot_frequency=None):
    schema = adapt_state_schema_for_mode(JobScoutState, mode, snapshot_frequency)

    async def classify(state: JobScoutState, runtime: Runtime):
        context = _context(runtime)
        user = _latest_user(state)
        text = _text(user.content)
        resumes = await run_assembly(read_uploaded_resumes, state, context.get("user_id"), context.get("thread_id")) if context.get("user_id") and context.get("thread_id") else {}
        snapshot = BASE_SNAPSHOTS.get_snapshot(context.get("jobscout_base_context_ref"), context.get("user_id"), context.get("thread_id"))
        classifier = fallback
        if classifier is None and app_config is not None:
            classifier = partial(classify_with_model, app_config=app_config, context=context)
        decision = await decide(
            text,
            anchor=state.get("jobscout_anchor"),
            has_resume=bool(resumes),
            has_base=bool(snapshot and snapshot.get("records")),
            mode_hint=context.get("jobscout_mode"),
            fallback=classifier,
        )
        update = {"jobscout_decision": asdict(decision)}
        if decision.in_scope:
            update["jobscout_anchor"] = decision.anchor
        return update

    def respond(state: JobScoutState, runtime: Runtime):
        decision = Decision(**state["jobscout_decision"])
        context = _context(runtime)
        return {
            "messages": [
                AIMessage(
                    fixed_reply(decision),
                    additional_kwargs={"jobscout_route": {"version": 3, "run_id": context.get("run_id", ""), **decision.public(), "source": decision.source}},
                )
            ]
        }

    async def research(state: JobScoutState, config: RunnableConfig, runtime: Runtime):
        from .agent import assemble_research_agent

        decision = Decision(**state["jobscout_decision"])
        context = {**_context(runtime), "jobscout_mode": decision.anchor["mode"], "jobscout_evidence_version": 2, "is_subagent": False}
        resumes = await run_assembly(read_uploaded_resumes, state, context.get("user_id"), context.get("thread_id"))
        snapshot = BASE_SNAPSHOTS.get_snapshot(context.get("jobscout_base_context_ref"), context.get("user_id"), context.get("thread_id")) or {}
        # Recheck expiring resources immediately before assembly.
        needs_resume = decision.anchor["mode"] == "base_match" or decision.intent == "add_resume"
        missing = (["resume"] if needs_resume and not resumes else []) + (["base_context"] if decision.anchor["mode"] == "base_match" and not snapshot.get("records") else [])
        if missing:
            decision.missing_fields = missing
            return {**respond({"jobscout_decision": asdict(decision)}, runtime), "jobscout_decision": asdict(decision)}
        factory = agent_factory or assemble_research_agent
        agent = await run_assembly(factory, mode=decision.anchor["mode"], context=context, app_config=app_config, resumes=resumes, anchor=decision.anchor)
        data = {"anchor": decision.anchor, "intent": decision.intent, "uploaded_files": ["/mnt/user-data/uploads/" + name for name in resumes]}
        if decision.anchor["mode"] == "base_match":
            data["base_records"] = snapshot
        elif decision.intent in {"follow_up_deepen", "add_resume"}:
            prior = next((message for message in reversed(state.get("messages", [])) if isinstance(message, AIMessage) and message.additional_kwargs.get("jobscout_evidence", {}).get("version") == 2), None)
            if prior:
                data["prior_report_for_research_direction_only"] = prior.content
        user = _latest_user(state)
        # Target/resume/job fields are human data, never interpolated in a system
        # prompt. A new company never receives previous target conversation.
        inputs = {"messages": [HumanMessage(json.dumps(data, ensure_ascii=False)), user]}
        result = await agent.ainvoke(inputs, config=config, context=context)
        # Only the checked final response goes back into the outer checkpoint.
        final = next((message for message in reversed(result.get("messages", [])) if isinstance(message, AIMessage) and not message.tool_calls), None)
        if final is None:
            raise RuntimeError("JobScout did not produce a final response")
        return {"messages": [final]}

    builder = StateGraph(schema)
    builder.add_node("classify", classify, input_schema=schema)
    builder.add_node("respond", respond, input_schema=schema)
    builder.add_node("research", research, input_schema=schema)
    builder.add_edge(START, "classify")
    builder.add_conditional_edges("classify", lambda state: "research" if not fixed_reply(Decision(**state["jobscout_decision"])) else "respond")
    builder.add_edge("respond", END)
    builder.add_edge("research", END)
    return builder.compile()


def assemble_jobscout(config, *, app_config=None):
    from deerflow.config import get_app_config
    from deerflow.runtime.checkpoint_mode import (
        INTERNAL_CHECKPOINT_MODE_KEY,
        freeze_checkpoint_channel_mode,
        freeze_checkpoint_snapshot_frequency,
        frozen_checkpoint_channel_mode,
        inject_checkpoint_mode,
    )

    app_config = app_config or (config.get("context") or {}).get("app_config") or get_app_config()
    requested = app_config.database.checkpoint_channel_mode
    if frozen_checkpoint_channel_mode() is not None:
        requested = (config.get("configurable") or {}).get(INTERNAL_CHECKPOINT_MODE_KEY, requested)
    mode = freeze_checkpoint_channel_mode(requested)
    frequency = freeze_checkpoint_snapshot_frequency(app_config.database.checkpoint_delta.snapshot_frequency)
    inject_checkpoint_mode(config, mode)
    return build_entry_graph(app_config=app_config, mode=mode, snapshot_frequency=frequency)
