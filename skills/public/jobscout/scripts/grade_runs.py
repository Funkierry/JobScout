#!/usr/bin/env python3
"""Grade JobScout run artifacts with deterministic gates and human scores."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = SKILL_ROOT / "evals" / "behavior_eval_set.json"

QUALITY_WEIGHTS = {
    "intent_flow": 15,
    "output_contract": 20,
    "evidence_facts": 25,
    "job_analysis": 15,
    "resume_base_grounding": 15,
    "reliability_cost": 10,
}

RESEARCH_TOOLS = {"web_search", "web_fetch", "task"}
BANNED_HOSTS = {
    "blind.com",
    "glassdoor.com",
    "indeed.com",
    "levels.fyi",
    "linkedin.com",
    "reddit.com",
    "wikipedia.org",
}

PREP_REQUIRED_H2 = ["公司速览", "岗位拆解", "面试题预测"]
PREP_FINAL_H2 = "证据边界与后续建议"
PREP_ALLOWED_H2 = set(PREP_REQUIRED_H2 + ["差距分析", PREP_FINAL_H2])
BASE_H2 = ["候选人画像", "推荐岗位", "匹配依据", "风险与数据边界"]

MANUAL_GATES = {
    "prep_report": ["no_fabrication", "sources_support_claims", "analysis_grounded"],
    "base_report": ["no_fabrication", "base_grounding"],
    "base_missing_input": ["no_fabrication"],
    "followup_gap": ["no_fabrication", "resume_grounding"],
    "tool_unavailable": ["no_fabrication"],
    "clarify_once": [],
    "boundary_response": [],
}

URL_RE = re.compile(r"https?://[^\s)>]+", re.IGNORECASE)
MARKDOWN_URL_RE = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
PRC_ID_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Gate:
    gate_id: str
    status: str
    message: str
    source: str = "automatic"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_runs(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL at line {line_number}: {exc}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"JSONL line {line_number} must be an object")
                rows.append(row)
        return rows

    payload = load_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("runs"), list):
        payload = payload["runs"]
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError("JSON input must be an array of run objects or an object with a runs array")
    return payload


def canonical_tool_name(raw_name: str) -> str:
    for candidate in ("web_search", "web_fetch", "read_file", "task", "ask_clarification"):
        if raw_name == candidate or raw_name.endswith(f"__{candidate}") or raw_name.endswith(f".{candidate}"):
            return candidate
    return raw_name


def tool_calls(run: dict[str, Any], name: str | None = None) -> list[dict[str, Any]]:
    calls = run.get("tool_calls", [])
    if not isinstance(calls, list):
        return []
    normalized = [call for call in calls if isinstance(call, dict) and isinstance(call.get("name"), str)]
    if name is None:
        return normalized
    return [call for call in normalized if canonical_tool_name(call["name"]) == name]


def heading_texts(markdown: str, level: int) -> list[str]:
    return [text.strip() for marks, text in HEADING_RE.findall(markdown) if len(marks) == level]


def markdown_section(markdown: str, level: int, title: str) -> str | None:
    lines = markdown.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match and len(match.group(1)) == level and match.group(2).strip() == title:
            start = index + 1
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[index])
        if match and len(match.group(1)) <= level:
            end = index
            break
    return "\n".join(lines[start:end])


def markdown_table_rows(section: str | None) -> list[list[str]]:
    if not section:
        return []
    raw_rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        raw_rows.append(cells)
    return raw_rows[1:] if raw_rows else []


def output_urls(output: str) -> list[str]:
    return URL_RE.findall(output)


def banned_output_hosts(output: str) -> list[str]:
    found: set[str] = set()
    for url in output_urls(output):
        host = (urlparse(url).hostname or "").lower()
        for banned in BANNED_HOSTS:
            if host == banned or host.endswith(f".{banned}"):
                found.add(host)
    return sorted(found)


def has_parallel_task_window(calls: list[dict[str, Any]]) -> bool | None:
    windows: list[tuple[float, float]] = []
    for call in calls:
        started = call.get("started_at_ms")
        ended = call.get("ended_at_ms")
        if not isinstance(started, (int, float)) or not isinstance(ended, (int, float)):
            return None
        if ended < started:
            return False
        windows.append((float(started), float(ended)))
    return max(start for start, _ in windows) < min(end for _, end in windows)


def gate(gate_id: str, condition: bool, pass_message: str, fail_message: str) -> Gate:
    return Gate(gate_id, "pass" if condition else "fail", pass_message if condition else fail_message)


def review_gate(gate_id: str, message: str) -> Gate:
    return Gate(gate_id, "review", message)


def gate_no_research_tools(run: dict[str, Any]) -> Gate:
    used = sorted({canonical_tool_name(call["name"]) for call in tool_calls(run)} & RESEARCH_TOOLS)
    return gate(
        "no_research_tools",
        not used,
        "未调用研究工具",
        f"调用了当前模式禁止的研究工具：{', '.join(used)}",
    )


def check_prep_report(run: dict[str, Any], case: dict[str, Any]) -> list[Gate]:
    output = run["output"]
    gates: list[Gate] = []
    tasks = tool_calls(run, "task")
    gates.append(gate("three_task_calls", len(tasks) == 3, "恰好调用 3 次 task", f"task 调用数为 {len(tasks)}，要求为 3"))
    gates.append(gate("no_clarification_after_complete_input", not tool_calls(run, "ask_clarification"), "未额外补问", "输入完整后仍调用了补问工具"))

    parallel = has_parallel_task_window(tasks) if len(tasks) == 3 else False
    if parallel is None:
        gates.append(review_gate("three_tasks_parallel", "task 轨迹缺少 started_at_ms / ended_at_ms，需人工确认并行性"))
    else:
        gates.append(gate("three_tasks_parallel", parallel, "三个 task 存在共同运行窗口", "三个 task 未并行运行"))

    h1 = heading_texts(output, 1)
    title_ok = len(h1) == 1 and bool(re.fullmatch(r".+\s+·\s+.+\s+面试准备包", h1[0]))
    gates.append(gate("prep_title", title_ok, "面试准备标题正确", "标题不符合“公司 · 岗位方向 面试准备包”"))

    h2 = heading_texts(output, 2)
    missing_h2 = [title for title in PREP_REQUIRED_H2 + [PREP_FINAL_H2] if title not in h2]
    extra_h2 = [title for title in h2 if title not in PREP_ALLOWED_H2]
    gates.append(gate("prep_required_sections", not missing_h2, "必需章节完整", f"缺少章节：{', '.join(missing_h2)}"))
    gates.append(gate("prep_no_extra_sections", not extra_h2, "没有越界二级章节", f"存在越界章节：{', '.join(extra_h2)}"))

    requires_gap = case.get("expected", {}).get("action") == "research_read_resume_and_report"
    has_gap = "差距分析" in h2
    expected_h2 = PREP_REQUIRED_H2 + (["差距分析"] if requires_gap else []) + [PREP_FINAL_H2]
    gates.append(gate("resume_read", not requires_gap or bool(tool_calls(run, "read_file")), "简历读取符合输入状态", "可读简历存在但没有调用 read_file"))
    gates.append(gate("resume_gap_section", has_gap == requires_gap, "简历差距章节符合输入状态", "差距分析与简历可读状态不一致"))
    gates.append(gate("prep_section_order", h2 == expected_h2, "面试报告二级章节顺序正确", f"二级章节顺序应为 {expected_h2}，实际为 {h2}"))

    valid_source_count = run.get("environment", {}).get("valid_source_count")
    if isinstance(valid_source_count, int) and valid_source_count > 0:
        tech_rows = markdown_table_rows(markdown_section(output, 3, "技术 / 岗位题"))
        behavior_rows = markdown_table_rows(markdown_section(output, 3, "行为题"))
        gates.append(gate("technical_question_count", len(tech_rows) >= 8, "技术或岗位题不少于 8 道", f"技术或岗位题只有 {len(tech_rows)} 道"))
        gates.append(gate("behavior_question_count", len(behavior_rows) >= 4, "行为题不少于 4 道", f"行为题只有 {len(behavior_rows)} 道"))
        rows = tech_rows + behavior_rows
        linked = bool(rows) and all(MARKDOWN_URL_RE.search(" | ".join(row)) for row in rows)
        gates.append(gate("question_rows_have_links", linked, "每道题均有可点击 URL", "存在没有可点击 URL 的题目行"))
    elif valid_source_count == 0:
        gates.append(Gate("question_minimum_only_with_sources", "pass", "无有效来源，不强制 8+4 题量"))
    else:
        gates.append(review_gate("question_minimum_only_with_sources", "缺少 environment.valid_source_count，无法判断题量门槛"))

    banned = banned_output_hosts(output)
    gates.append(gate("no_banned_source_domains", not banned, "未出现禁用来源域名", f"出现禁用来源域名：{', '.join(banned)}"))
    return gates


FIELD_ALIASES = {
    "目标公司": ("目标公司", "公司"),
    "岗位方向": ("岗位方向", "岗位"),
    "招聘类型": ("招聘类型", "校招", "社招", "实习"),
    "简历": ("简历", "附件"),
    "系统注入岗位记录": ("岗位记录", "岗位数据", "系统注入"),
}


def mentions_field(output: str, field: str) -> bool:
    return any(alias in output for alias in FIELD_ALIASES.get(field, (field,)))


def check_clarify_once(run: dict[str, Any], case: dict[str, Any]) -> list[Gate]:
    output = run["output"]
    missing = case.get("expected", {}).get("missing_fields", [])
    return [
        gate_no_research_tools(run),
        gate("no_report_before_required_input", "面试准备包" not in output, "未提前生成报告", "缺少必填信息时仍生成了报告"),
        gate("mentions_only_missing_required_fields", all(mentions_field(output, field) for field in missing), "补问包含所有缺失必填项", "补问没有覆盖所有缺失必填项"),
    ]


def check_base_report(run: dict[str, Any], case: dict[str, Any]) -> list[Gate]:
    output = run["output"]
    h1 = heading_texts(output, 1)
    h2 = heading_texts(output, 2)
    rows = markdown_table_rows(markdown_section(output, 2, "推荐岗位"))
    scores_ok = True
    record_ids_ok = True
    for row in rows:
        if len(row) < 7:
            scores_ok = False
            record_ids_ok = False
            continue
        score_match = re.search(r"\d+(?:\.\d+)?", row[1])
        if not score_match or not 0 <= float(score_match.group()) <= 100:
            scores_ok = False
        if not row[-1] or row[-1] in {"-", "—", "无"}:
            record_ids_ok = False
    sensitive = sorted(set(PHONE_RE.findall(output) + PRC_ID_RE.findall(output)))
    return [
        gate_no_research_tools(run),
        gate("base_resume_read", bool(tool_calls(run, "read_file")), "读取了候选人简历", "完整 Base 匹配没有调用 read_file"),
        gate("base_title", h1 == ["简历 × 飞书岗位匹配报告"], "Base 报告标题正确", "Base 报告标题错误或不唯一"),
        gate("base_exact_sections", h2 == BASE_H2, "Base 四个二级章节准确且顺序正确", f"Base 二级章节不符合契约：{h2}"),
        gate("base_recommendation_limit", 1 <= len(rows) <= 10, "推荐岗位数量在 1 至 10 之间", f"推荐岗位数量为 {len(rows)}"),
        gate("base_scores_in_range", bool(rows) and scores_ok, "推荐分数均在 0 至 100 之间", "推荐表存在缺列或越界分数"),
        gate("base_record_ids", bool(rows) and record_ids_ok, "推荐岗位均保留记录标识", "推荐岗位缺少可追溯记录标识"),
        gate("no_sensitive_values", not sensitive, "未输出手机号或身份证号", f"输出疑似敏感值：{', '.join(sensitive)}"),
    ]


def check_base_missing_input(run: dict[str, Any], case: dict[str, Any]) -> list[Gate]:
    output = run["output"]
    missing = case.get("expected", {}).get("missing_fields", [])
    return [
        gate_no_research_tools(run),
        gate("no_empty_base_report", "# 简历 × 飞书岗位匹配报告" not in output, "未生成空匹配报告", "缺少输入时仍生成了匹配报告"),
        gate("mentions_missing_base_input", all(mentions_field(output, field) for field in missing), "明确说明缺失输入", "没有明确说明全部缺失输入"),
    ]


def check_followup_gap(run: dict[str, Any], case: dict[str, Any]) -> list[Gate]:
    output = run["output"]
    research_calls = [call for call in tool_calls(run) if canonical_tool_name(call["name"]) in RESEARCH_TOOLS]
    return [
        gate("resume_read", bool(tool_calls(run, "read_file")), "追问中读取了简历", "追问要求差距分析但没有读取简历"),
        gate("reuse_existing_research", not research_calls, "复用了已有研究上下文", "目标未改变却重新调用研究工具"),
        gate("gap_analysis_present", "差距分析" in output, "输出包含差距分析", "输出缺少差距分析"),
        gate("no_sensitive_values", not PHONE_RE.search(output) and not PRC_ID_RE.search(output), "未输出敏感值", "输出疑似手机号或身份证号"),
    ]


def check_tool_unavailable(run: dict[str, Any], case: dict[str, Any]) -> list[Gate]:
    output = run["output"]
    explains_failure = any(term in output for term in ("无法", "不可用", "未加载", "缺少"))
    return [
        gate_no_research_tools(run),
        gate("explains_tool_unavailability", explains_failure, "说明了工具不可用", "没有说明工具不可用"),
        gate("no_fabricated_report_without_tools", "面试准备包" not in output, "未在无工具时生成完整报告", "无研究工具时仍生成了完整报告"),
    ]


def check_boundary_response(run: dict[str, Any], case: dict[str, Any]) -> list[Gate]:
    output = run["output"]
    return [
        gate("no_jobscout_tools", not tool_calls(run), "未启动 JobScout 工具", "边界任务调用了工具"),
        gate("no_jobscout_report", "面试准备包" not in output and "飞书岗位匹配报告" not in output, "未生成 JobScout 报告", "边界任务错误生成了 JobScout 报告"),
    ]


PROFILE_CHECKERS = {
    "prep_report": check_prep_report,
    "clarify_once": check_clarify_once,
    "base_report": check_base_report,
    "base_missing_input": check_base_missing_input,
    "followup_gap": check_followup_gap,
    "tool_unavailable": check_tool_unavailable,
    "boundary_response": check_boundary_response,
}


def validate_run_shape(run: dict[str, Any], known_case_ids: set[str], run_ids: set[str]) -> None:
    required_strings = ("case_id", "run_id", "output")
    for field in required_strings:
        if not isinstance(run.get(field), str) or not run[field].strip():
            raise ValueError(f"run requires non-empty string field {field!r}")
    if run["case_id"] not in known_case_ids:
        raise ValueError(f"unknown case_id {run['case_id']!r}")
    if run["run_id"] in run_ids:
        raise ValueError(f"duplicate run_id {run['run_id']!r}")
    run_ids.add(run["run_id"])
    if not isinstance(run.get("tool_calls"), list):
        raise ValueError(f"run {run['run_id']!r} requires tool_calls array")
    runtime = run.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError(f"run {run['run_id']!r} requires runtime object")
    for field in ("model", "skill_digest", "started_at"):
        if not isinstance(runtime.get(field), str) or not runtime[field].strip():
            raise ValueError(f"run {run['run_id']!r} requires runtime.{field}")


def add_manual_gates(gates: list[Gate], profile: str, run: dict[str, Any]) -> None:
    review = run.get("human_review")
    hard_gates = review.get("hard_gates", {}) if isinstance(review, dict) else {}
    for gate_id in MANUAL_GATES[profile]:
        value = hard_gates.get(gate_id) if isinstance(hard_gates, dict) else None
        if value is True:
            gates.append(Gate(gate_id, "pass", "人工硬门槛复核通过", "human"))
        elif value is False:
            gates.append(Gate(gate_id, "fail", "人工硬门槛复核失败", "human"))
        else:
            gates.append(Gate(gate_id, "review", "等待人工硬门槛复核", "human"))


def quality_score(run: dict[str, Any]) -> tuple[float | None, list[str]]:
    review = run.get("human_review")
    scores = review.get("scores") if isinstance(review, dict) else None
    if not isinstance(scores, dict):
        return None, ["缺少 human_review.scores"]
    errors: list[str] = []
    weighted = 0.0
    active_weight = 0
    for dimension, weight in QUALITY_WEIGHTS.items():
        if dimension not in scores:
            errors.append(f"缺少评分维度 {dimension}")
            continue
        value = scores[dimension]
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 5:
            errors.append(f"{dimension} 必须为 0-5 或 null")
            continue
        weighted += weight * float(value) / 5
        active_weight += weight
    if errors:
        return None, errors
    if active_weight == 0:
        return None, ["所有质量维度均为 N/A"]
    return round(weighted * 100 / active_weight, 2), []


def evaluate_run(run: dict[str, Any], case: dict[str, Any], threshold: float) -> dict[str, Any]:
    profile = case["expected"]["profile"]
    checker = PROFILE_CHECKERS[profile]
    gates = checker(run, case)
    add_manual_gates(gates, profile, run)
    score, score_errors = quality_score(run)

    failures = [item for item in gates if item.status == "fail"]
    unresolved = [item for item in gates if item.status == "review"]
    if failures:
        status = "hard_fail"
    elif unresolved or score is None:
        status = "needs_human_review"
    elif score >= threshold:
        status = "passed"
    else:
        status = "quality_fail"

    metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
    return {
        "case_id": case["id"],
        "run_id": run["run_id"],
        "mode": case["mode"],
        "category": case["category"],
        "risk": case["risk"],
        "tier": case["tier"],
        "tags": list(case.get("tags", [])),
        "profile": profile,
        "status": status,
        "hard_gates": [asdict(item) for item in gates],
        "quality_score": score,
        "score_errors": score_errors,
        "metrics": {
            "input_tokens": metrics.get("input_tokens"),
            "output_tokens": metrics.get("output_tokens"),
            "latency_ms": metrics.get("latency_ms"),
        },
    }


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def metric_values(results: Iterable[dict[str, Any]], metric: str) -> list[float]:
    values: list[float] = []
    for result in results:
        value = result["metrics"].get(metric)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def compact_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(result["status"] for result in results)
    completed = counts["passed"] + counts["quality_fail"] + counts["hard_fail"]
    scores = [float(result["quality_score"]) for result in results if result["quality_score"] is not None]
    latencies = metric_values(results, "latency_ms")
    input_tokens = metric_values(results, "input_tokens")
    output_tokens = metric_values(results, "output_tokens")
    return {
        "runs": len(results),
        "passed": counts["passed"],
        "hard_failed": counts["hard_fail"],
        "quality_failed": counts["quality_fail"],
        "needs_human_review": counts["needs_human_review"],
        "review_complete_rate": round(completed / len(results), 4) if results else 0.0,
        "pass_rate_among_completed": round(counts["passed"] / completed, 4) if completed else None,
        "mean_quality_score": round(statistics.fmean(scores), 2) if scores else None,
        "latency_ms_p50": round(percentile(latencies, 0.5), 2) if latencies else None,
        "latency_ms_p95": round(percentile(latencies, 0.95), 2) if latencies else None,
        "total_input_tokens": int(sum(input_tokens)) if input_tokens else None,
        "total_output_tokens": int(sum(output_tokens)) if output_tokens else None,
    }


def slice_results(results: list[dict[str, Any]], field: str) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        buckets[str(result[field])].append(result)
    return {name: compact_summary(rows) for name, rows in sorted(buckets.items())}


def slice_tags(results: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        for tag in result.get("tags", []):
            buckets[str(tag)].append(result)
    return {name: compact_summary(rows) for name, rows in sorted(buckets.items())}


def build_report(results: list[dict[str, Any]], dataset: dict[str, Any], threshold: float) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "suite": dataset.get("suite"),
            "schema_version": dataset.get("schema_version"),
        },
        "quality_threshold": threshold,
        "summary": compact_summary(results),
        "slices": {
            "mode": slice_results(results, "mode"),
            "category": slice_results(results, "category"),
            "risk": slice_results(results, "risk"),
            "tier": slice_results(results, "tier"),
            "tag": slice_tags(results),
        },
        "results": results,
    }


def markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# JobScout 评测报告",
        "",
        f"- 数据集：`{report['dataset']['suite']}`",
        f"- 质量通过线：{report['quality_threshold']:.0f}",
        f"- 总运行数：{summary['runs']}",
        f"- 通过：{summary['passed']}；硬门槛失败：{summary['hard_failed']}；质量分失败：{summary['quality_failed']}；待人工复核：{summary['needs_human_review']}",
        f"- 已完成复核运行中的通过率：{format_rate(summary['pass_rate_among_completed'])}",
        f"- 平均质量分：{format_number(summary['mean_quality_score'])}",
        f"- 延迟 p50 / p95：{format_number(summary['latency_ms_p50'])} / {format_number(summary['latency_ms_p95'])} ms",
        "",
        "## 风险切片",
        "",
        "| 风险 | 运行数 | 通过 | 硬失败 | 质量失败 | 待复核 | 已复核通过率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for risk, values in report["slices"]["risk"].items():
        lines.append(
            f"| {risk} | {values['runs']} | {values['passed']} | {values['hard_failed']} | "
            f"{values['quality_failed']} | {values['needs_human_review']} | {format_rate(values['pass_rate_among_completed'])} |"
        )

    failures = [result for result in report["results"] if result["status"] != "passed"]
    lines.extend(["", "## 未通过与待复核", ""])
    if not failures:
        lines.append("无。")
    else:
        for result in failures:
            lines.append(f"### {result['run_id']} · {result['status']}")
            lines.append("")
            failed_gates = [gate for gate in result["hard_gates"] if gate["status"] != "pass"]
            for item in failed_gates:
                lines.append(f"- `{item['gate_id']}` [{item['status']}]：{item['message']}")
            for error in result["score_errors"]:
                lines.append(f"- `quality_score` [review]：{error}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_rate(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def format_number(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Run artifacts in JSONL or JSON format")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--quality-threshold", type=float, default=80.0)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--fail-on-regression", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 <= args.quality_threshold <= 100:
        raise SystemExit("--quality-threshold must be between 0 and 100")
    try:
        dataset = load_json(args.dataset)
        cases = {case["id"]: case for case in dataset["cases"]}
        runs = load_runs(args.input)
        seen_run_ids: set[str] = set()
        for run in runs:
            validate_run_shape(run, set(cases), seen_run_ids)
        results = [evaluate_run(run, cases[run["case_id"]], args.quality_threshold) for run in runs]
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    report = build_report(results, dataset, args.quality_threshold)
    if args.output_json:
        write_text(args.output_json, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.output_md:
        write_text(args.output_md, markdown_report(report))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))

    if args.fail_on_regression and any(result["status"] != "passed" for result in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
