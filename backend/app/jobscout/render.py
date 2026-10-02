"""Fixed report templates. No model Markdown is ever report structure."""

import re

from .matching import DIMENSION_LABELS


def cell(value, limit=4000):
    """Single-line text: neutralize Markdown, HTML, and table delimiters."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value[:limit].split())
    return re.sub(r"([\\*_{}\[\]()#+.!|<>" + chr(96) + r"])", r"\\\1", text)


def source(item):
    kind = "搜索摘要" if item["source_kind"] == "search_snippet" else "抓取正文"
    return f"[{kind}](<{item['url']}>)"


def render_prep(payload, items, evidence):
    payload = payload if isinstance(payload, dict) else {}
    company = cell(payload.get("company"), 120) or "目标公司"
    role = cell(payload.get("role"), 120) or "目标岗位"
    recruitment = cell(payload.get("recruitment_type"), 40) or "未注明"
    lines = [f"# {company} · {role} 面试准备包", "", f"> 招聘类型：{recruitment} · 生成日期：{evidence.as_of.isoformat()}", "", "> 以下为模型解读与可核验原文。逐字校验不等于语义正确，仍需结合上下文复核。", "", "## 公司速览", ""]

    def section(heading, name):
        lines.extend([heading, ""])
        rows = [item for item in items if item["section"] == name]
        if not rows:
            lines.extend(["公开证据不足，本节不补写推测内容。", ""])
            return
        lines.extend(["| 解读 / 题目 | 原文证据 | 来源 |", "|---|---|---|"])
        for item in rows:
            prefix = f"{item['published_at']} · " if name == "news" else ""
            label = "基于公开材料推导，非公司已考真题：" if name in {"technical", "behavioral"} else ""
            quote = cell(item["quote"])
            if name == "gap":
                quote += "；简历：" + cell(item["resume_quote"])
            lines.append(f"| {prefix}{label}{cell(item['claim'], 2000)} | {quote} | {source(item)} |")
        lines.append("")

    section("### 业务与产品", "business")
    section("### 近期动态", "news")
    section("### 融资 / 规模", "scale")
    lines.extend(["## 岗位拆解", ""])
    for title, name in [("必备技能", "required"), ("加分项", "preferred"), ("核心职责", "duties"), ("公司技术栈核对", "stack")]:
        section("### " + title, name)
    lines.extend(["## 面试题预测", ""])
    section("### 技术 / 岗位题", "technical")
    section("### 行为题", "behavioral")
    if any(item["section"] == "gap" for item in items):
        section("## 差距分析", "gap")
    lines.extend(
        [
            "## 证据边界与后续建议",
            "",
            f"- 本次保留 {len(items)} 条结构化证据；拒绝 {sum(evidence.rejected.values())} 条候选或格式错误。",
            "- 仅校验本次工具返回的 URL、原文片段与近期日期；搜索摘要不代表取得全文。",
            "- 近期指服务器日期往前 12 个自然月；日期原文存在不保证它就是文章发布时间，需人工复核。",
            "- 允许少量题或零题；证据不足的小节已明确说明。简历差距仅展示有简历原文依据的条目。",
        ]
    )
    return "\n".join(lines)


def render_matches(matches, evidence):
    lines = [
        "# 简历 × 飞书岗位匹配报告",
        "",
        "## 候选人画像",
        "",
        "仅依据本线程成功读取的简历片段计分，逐项依据见下文。",
        "",
        "## 推荐岗位",
        "",
        "| 排名 | 匹配度 | 公司 / 岗位 | 核心匹配 | 主要差距 | 建议动作 | 记录标识 |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(matches, 1):
        record = row["record"]
        company = next((value for key, value in record.items() if any(word in key.lower() for word in ("公司", "企业", "company")) and isinstance(value, str)), "")
        role = next((value for key, value in record.items() if any(word in key.lower() for word in ("岗位", "职位", "职务", "role", "position", "job")) and isinstance(value, str)), "")
        lines.append(f"| {index} | {row['total']}/100 | {cell(company, 200)} / {cell(role, 300)} | 见逐项依据 | 未核验项不计分 | 人工复核岗位要求 | {cell(row['record_id'], 200)} |")
    if not matches:
        lines.extend(["", "缺少本次授权岗位快照、可校验简历或有效结构化结果，无法给出推荐。"])
    lines.extend(["", "## 匹配依据", ""])
    for row in matches:
        lines.extend([f"### {cell(row['record_id'], 200)}", "", "| 维度 | 得分 | 简历原文 |", "|---|---|---|"])
        for item in row["score_items"]:
            quote = cell(item["resume_quote"]) if not item["reason"] else "未通过证据或分值校验，不计分"
            lines.append(f"| {DIMENSION_LABELS[item['dimension']]} | {item['points']} | {quote} |")
        lines.append("")
    lines.extend(
        [
            "## 风险与数据边界",
            "",
            "- 总分由代码重算；缺失、重复维度、越界分值或无逐字简历依据的项均为 0 分。",
            f"- 本次校验范围为已读取快照的 {len(evidence.records)} 条岗位；尚有未读分页：{'是' if evidence.base_bounds.get('has_more') else '否'}；上下文裁剪：{'是' if evidence.base_bounds.get('context_truncated') else '否'}。",
            "- 岗位来自当前用户、本线程的服务器快照；简历依据来自成功读取的已上传文件。",
            "- 片段真实存在不等于分数合理；分值仍为模型建议，需人工复核。未提供依据的岗位不进入排名。",
        ]
    )
    return "\n".join(lines)
