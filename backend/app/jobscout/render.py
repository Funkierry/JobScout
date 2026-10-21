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
    lines.extend(["", "### 准备行动", "", "以下是建议动作，不是公司面试安排；优先级仅用于安排准备顺序，不代表考试概率。", "", "| 顺序 | 岗位依据 | 可用个人经历 | 建议动作 / 待补材料 |", "|---|---|---|---|"])
    priorities = [item for name in ("gap", "required", "duties") for item in items if item["section"] == name][:3]
    for index, item in enumerate(priorities, 1):
        experience = cell(item.get("resume_quote", "")) or "本条没有已校验的个人经历，请自行补充"
        lines.append(f"| {index} | {cell(item['quote'])} · {source(item)} | {experience} | 围绕该要求整理一个真实案例，补充个人职责、具体行动和可核对的结果 |")
    if not priorities:
        lines.append("| 1 | 未取得可用岗位要求原文 | 尚无可对应的个人经历 | 补充岗位 JD 与简历，再生成针对性的准备内容 |")
    lines.extend(["", "- 先核对上述岗位原文，再选择真实经历；不补写未经提供的成果。", "- 练习本报告中标为推导的问题，记录回答要点与待核对资料；推导题不等于公司真题。", "- 准备进度与个人笔记可在报告下方保存，独立于这份证据报告。"])
    return "\n".join(lines)


def render_matches(matches, evidence, *, rejection_counts=None, excluded=None):
    lines = [
        "# 简历 × 飞书岗位匹配报告",
        "",
        "## 候选人画像",
        "",
        "仅依据本线程成功读取的简历及授权岗位原文进行对照，逐项依据见下文。",
        "",
        "## 推荐岗位",
        "",
        "依据分用于排列已提供对应证据的岗位，不是匹配概率或录用概率。缺少证据不等于不符合要求。",
        "",
        "| 排名 | 核对分组 | 公司 / 岗位 | 核心匹配 | 待核对项 | 建议动作 | 记录标识 |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(matches, 1):
        record = row["record"]
        company = next((value for key, value in record.items() if any(word in key.lower() for word in ("公司", "企业", "company")) and isinstance(value, str)), "")
        role = next((value for key, value in record.items() if any(word in key.lower() for word in ("岗位", "职位", "职务", "role", "position", "job")) and isinstance(value, str)), "")
        supported = "、".join(DIMENSION_LABELS[item["dimension"]] for item in row["score_items"] if item["points"] > 0)
        pending = "、".join(DIMENSION_LABELS[item["dimension"]] for item in row["score_items"] if item["points"] == 0) or "具体满足程度"
        needs_info = any(item.get("judgement", "unknown") != "supported" for item in [*row["score_items"], *row.get("requirement_checks", [])])
        group = "需要补充信息" if needs_info else "有依据，可进一步核对"
        lines.append(f"| {index} | {group} | {cell(company, 200)} / {cell(role, 300)} | {supported}有对应原文 | {pending}待核对 | 补充缺项并核对岗位要求 | {cell(row['record_id'], 200)} |")
    if not matches:
        lines.extend(["", "没有通过双侧证据校验的推荐岗位。请补充岗位要求或简历材料，或核对明确的学历、经验冲突；不使用零分岗位凑数。"])
    lines.extend(["", "## 匹配依据", ""])
    labels = {**DIMENSION_LABELS, "experience": "工作经验"}
    judgements = {"supported": "有支持", "limited": "支持有限", "not_met": "明确不满足", "unknown": "待确认"}

    def comparison(row):
        lines.extend(["| 要求 / 字段 | 岗位原文 | 简历原文 / 文件 | 判断 | 下一步 |", "|---|---|---|---|---|"])
        for item in [*row.get("requirement_checks", []), *row["score_items"]]:
            state = item.get("judgement", "unknown")
            action = {"supported": "核对适用范围与具体满足程度", "limited": "补充能证明熟练度的项目或工作经历", "not_met": "核对门槛或选择其他岗位", "unknown": "补充该项岗位要求或简历依据后重新匹配"}[state]
            requirement = cell(item.get("requirement_quote", "")) or "未取得可核对的要求片段"
            resume = cell(item.get("resume_quote", "")) or "未取得可用简历依据；不代表不符合"
            if item.get("resume_file"):
                resume += "（" + cell(item["resume_file"], 200) + "）"
            field = cell(item.get("requirement_field", ""))
            score = f" · {item['points']} 分" if "points" in item else ""
            lines.append(f"| {labels[item['dimension']]}{(' / ' + field) if field else ''} | {requirement} | {resume} | {judgements[state]}{score} | {action} |")
        lines.append("")

    for row in matches:
        lines.extend([f"### {cell(row['record_id'], 200)}", "", f"依据分：{row['total']}/100，仅用于排序。", ""])
        comparison(row)
    if excluded:
        lines.extend(["### 未入推荐的岗位", "", "仅展示本轮校验过的前 10 个未入推荐岗位；不是完整岗位库的淘汰清单。", ""])
        for row in excluded[:10]:
            reason = "存在明确的学历或经验最低门槛冲突" if row["reason"] in {"education_conflict", "experience_conflict"} else "没有可计入排名的正分依据"
            identity = " / ".join(cell(value, 200) for key, value in row["record"].items() if re.search(r"^(公司|企业|岗位|职位|职务|company|role|position|job.?title)$", key, re.I) and isinstance(value, str))
            lines.extend([f"#### {cell(row['record_id'], 200)}", "", identity, "", reason + "。", ""])
            comparison(row)
    lines.extend(
        [
            "## 风险与数据边界",
            "",
            "- 总分由代码重算；缺失、重复维度、跨维度复用同一简历片段、越界分值或缺少岗位/简历双侧原文的项均不计分。零分岗位不进入排名。",
            f"- 本次校验范围为已读取快照的 {len(evidence.records)} 条岗位；尚有未读分页：{'是' if evidence.base_bounds.get('has_more') else '否'}；上下文裁剪：{'是' if evidence.base_bounds.get('context_truncated') else '否'}。",
            "- 岗位来自当前用户、本线程的服务器快照；简历依据来自成功读取的已上传文件。",
            "- 对应关键词、明确学历和经验冲突采用有限规则检查；语义、熟练程度及未覆盖的硬性要求仍需人工复核，分值仍为模型建议。信息未知不等于不满足。",
            "- 要求对照只覆盖本轮提交的维度与规则可识别的学历、经验最低门槛，未逐条覆盖完整 JD；有支持不代表已满足全部条件。学历和经验对照不额外加分。",
            f"- 本轮因明确学历/经验冲突未入榜 {(rejection_counts or {}).get('education_conflict', 0) + (rejection_counts or {}).get('experience_conflict', 0)} 个；"
            f"因无有效正分依据未入榜 {(rejection_counts or {}).get('no_positive_evidence', 0)} 个。",
            "",
            "### 准备行动",
            "",
            "1. 优先核对要求对照中标为待确认或支持有限的项目，补充岗位 JD 或对应简历材料；这不是不符合要求的结论。",
            "2. 从已有简历原文选择一个相关经历，补充个人职责、具体行动与可核对的结果；不编写未提供的经历。",
            "3. 核对后再决定接受或放弃推荐，并在报告下方记录原因；需要面试准备时保存目标岗位并继续准备。",
        ]
    )
    return "\n".join(lines)
