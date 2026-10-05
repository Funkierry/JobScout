"""Admitted JobScout graphs with explicit tools, no generic lead assembly."""

import asyncio
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from .budget import JobScoutBudgetMiddleware, RunTokenBudget
from .middleware import JobScoutLinkMiddleware
from .tool_policy import JobScoutToolPolicy


def load_tools(mode, *, app_config, resumes):
    @tool
    def read_file(path: str) -> str:
        """Read an authorized upload using /mnt/user-data/uploads/<filename>."""
        allowed = {"/mnt/user-data/uploads/" + name: text for name, text in resumes.items()}
        return allowed.get(path, "无法读取：请使用当前线程已上传文件的完整虚拟路径。")

    selected = [read_file]
    if mode == "interview_prep":
        from deerflow.reflection import resolve_variable

        for item in app_config.tools:
            if item.name not in {"web_search", "web_fetch"}:
                continue
            loaded = resolve_variable(item.use, BaseTool)
            if loaded.name != item.name:
                raise ValueError("JobScout configured tool name mismatch")
            if all(existing.name != loaded.name for existing in selected):
                selected.append(loaded)
    return selected


def assemble_research_agent(*, mode, context, app_config, resumes, child=False, anchor=None, budget=None):
    from deerflow.agents.lead_agent.agent import _authorize_model_name
    from deerflow.authz.tool_filter import apply_tool_authorization
    from deerflow.models.factory import create_chat_model
    from deerflow.utils.assembly_io import run_assembly

    budget = budget if budget is not None else RunTokenBudget(app_config.token_budget)
    model_name = context.get("model_name") or (app_config.models[0].name if app_config.models else None)
    if not model_name or app_config.get_model_config(model_name) is None:
        raise ValueError("JobScout requires a configured main model")
    model_name = _authorize_model_name(model_name, context=context, app_config=app_config)
    model = create_chat_model(name=model_name, app_config=app_config, thinking_enabled=False, attach_tracing=False)
    tools = load_tools(mode, app_config=app_config, resumes=resumes)
    if mode == "interview_prep" and not child:
        semaphore = asyncio.Semaphore(3)
        remaining = 3

        @tool
        async def task(prompt: str, config: RunnableConfig, description: str = "", subagent_type: str = "general-purpose") -> str:
            """Run one of the three research branches (company, role/JD, interview evidence)."""
            nonlocal remaining
            # No awaits between checking/decrementing; parallel tool calls cannot
            # exceed the per-run budget. No recursive task tool on children.
            if remaining <= 0 or subagent_type != "general-purpose":
                return "研究子任务额度已用完或子任务类型不受支持。"
            remaining -= 1
            async with semaphore, asyncio.timeout(180):
                child_context = {**context, "is_subagent": True}
                agent = await run_assembly(assemble_research_agent, mode=mode, context=child_context, app_config=app_config, resumes=resumes, child=True, anchor=anchor, budget=budget)
                result = await agent.ainvoke({"messages": [HumanMessage(prompt[:20000])]}, config={**config, "recursion_limit": min(config.get("recursion_limit", 50), 50)}, context=child_context)
                final = next((message for message in reversed(result.get("messages", [])) if isinstance(message, AIMessage) and not message.tool_calls), None)
                return final.content if final and isinstance(final.content, str) else "研究子任务未返回结构化证据。"

        tools.append(task)
    tools, _ = apply_tool_authorization(tools, context=context, app_config=app_config)
    policy = JobScoutToolPolicy(mode, child=child)
    # This trusted repository file is static instructions. Runtime data only
    # travels in human messages and actual tool responses.
    skill = Path(__file__).resolve().parents[3] / "skills/public/jobscout/SKILL.md"
    system = skill.read_text(encoding="utf-8") + "\n由入口代码完成分类与必填项校验。只执行当前已批准任务。"
    if child:
        system += "\n你是研究子任务。只输出研究结果及 jobscout_evidence JSON；不再委派，不输出根报告。"
    return create_agent(
        model=model,
        tools=policy.filter_tools(tools),
        system_prompt=system,
        middleware=[policy, JobScoutLinkMiddleware(trusted_resumes=resumes, trusted_anchor=anchor), JobScoutBudgetMiddleware(budget)],
        checkpointer=False,
        name="jobscout_research_child" if child else "jobscout_research",
    )
