"""Pure, offline-first entry classification. No tool or model imports here."""

import re
from dataclasses import asdict, dataclass, field

INTENTS = frozenset({"interview_prep", "base_match", "follow_up_deepen", "switch_company", "add_resume", "off_topic"})
FIELDS = ("company", "role", "recruitment_type")
LABELS = {"company": "目标公司", "role": "岗位方向", "recruitment_type": "招聘类型（校招/社招/实习）", "resume": "可读取的上传简历", "base_context": "当前线程的飞书岗位表"}
OFF_TOPIC = "JobScout 支持中国大陆岗位面试准备和飞书岗位库匹配。请提供目标公司、岗位方向及招聘类型；其他问题请新开普通对话。"
UNRESOLVED = "暂时无法确认任务。请说明要做面试准备还是飞书岗位匹配；面试准备请补充目标公司、岗位方向和招聘类型。"
ROLE_PATTERN = r"(?:后端(?:开发|工程师)?|前端(?:开发|工程师)?|算法工程师|数据分析(?:师)?|数据工程师|大数据(?:开发|工程师)?|产品经理|测试(?:开发|工程师)?|运维(?:工程师)?|软件工程师|软件开发|AI\s*工程师|运营|设计师|software engineering)"


@dataclass
class Decision:
    in_scope: bool
    intent: str
    missing_fields: list[str] = field(default_factory=list)
    anchor: dict = field(default_factory=dict)
    source: str = "rules"

    def public(self):
        return {key: value for key, value in asdict(self).items() if key in {"in_scope", "intent", "missing_fields"}}


def clean_text(text):
    return re.sub(r"^\s*/jobscout\s*", "", text or "").strip()


def extract_fields(text):
    """Accept bounded verbatim fields; never guess unnamed employers."""
    result = {}
    for key, label in (("company", r"(?:目标)?公司"), ("role", r"岗位(?:方向)?"), ("recruitment_type", r"招聘类型")):
        match = re.search(label + r"\s*[:：]\s*([^；;，,。\n]{1,60})", text)
        if match:
            result[key] = match[1].strip()
    kinds = [kind for kind in ("校招", "社招", "实习") if kind in text]
    if not kinds and re.search(r"\bcampus\b", text, re.I):
        kinds = ["校招"]
    if len(kinds) == 1:
        result["recruitment_type"] = kinds[0]
    if result.get("recruitment_type") not in {"校招", "社招", "实习"}:
        result.pop("recruitment_type", None)
    role = re.search(ROLE_PATTERN, text, re.I)
    if role and "role" not in result:
        result["role"] = role[0]
    switch = re.search(r"(?:目标)?公司(?:改成|换成|改为|换为|切换到)\s*([^，,。；;\s]{1,40})", text)
    if switch:
        result["company"] = switch[1]
    if "company" not in result and role:
        prefix = text[: role.start()]
        match = re.search(r"(?:准备|我要面|要面|这是|那份|目标公司为)\s*([^，,。；;：:\s]{1,40}?)的?(?:校招|社招|实习)?$", prefix)
        if match:
            company = re.sub(r"(?:的|校招|社招|实习)$", "", match[1])
            if company:
                result["company"] = company
        elif match := re.search(r"\binterview at ([\w .-]{1,40}?)(?: in |[,.;]|$)", text, re.I):
            result["company"] = match[1].strip()
    for key in tuple(result):
        if any(word in result[key] for word in ("没想好", "待定", "不确定", "互联网公司")):
            result.pop(key)
    return result


def _finish(intent, fields, anchor, has_resume, has_base, source="rules"):
    previous = {key: value for key, value in (anchor or {}).items() if key in (*FIELDS, "mode") and isinstance(value, str)}
    mode = "base_match" if intent == "base_match" else previous.get("mode", "interview_prep") if intent in {"follow_up_deepen", "add_resume"} else "interview_prep"
    updated = {**previous, **fields, "mode": mode}
    missing = [key for key in FIELDS if not updated.get(key)] if mode == "interview_prep" else []
    if (mode == "base_match" or intent == "add_resume") and not has_resume:
        missing.append("resume")
    if mode == "base_match" and not has_base:
        missing.append("base_context")
    return Decision(True, intent, missing, updated, source)


def classify_rules(text, *, anchor=None, has_resume=False, has_base=False, mode_hint=None):
    text = clean_text(text)
    # A selector is a preference, never a blanket scope bypass.
    blocked = r"忽略(?:所有|以上|之前)?规则|绕过|编造(?:经历|证据)|不(?:要|用)(?:任何)?来源|批量投递|登录.*网站|求职信|润色.*简历|简历.*润色|库存|SKU|论文|咖啡店|税前|到手|翻译|重复渲染|读取密码"
    if re.search(blocked, text, re.I):
        return Decision(False, "off_topic", anchor=dict(anchor or {}))
    if re.search(r"(?<!面试)(?:解释一下|汇总.*新闻)|这道系统设计面试题", text) and not (anchor and re.search(r"刚才|报告|继续", text)):
        return Decision(False, "off_topic", anchor=dict(anchor or {}))
    fields = extract_fields(text)
    explicit_prep = bool(re.search(r"面试准备|准备.*面试|我要面|要准备.*面试|prepare.*interview", text, re.I))
    explicit_base = bool(re.search(r"(?:飞书|base|岗位库).*岗位匹配|简历.*(?:岗位|职位).*匹配", text, re.I))
    switch = bool(re.search(r"(?:公司|岗位|方向|招聘类型)(?:改成|换成|改为|换为|切换到)", text))
    if explicit_base or (mode_hint == "base_match" and (not text or re.search(r"^(?:岗位匹配|请按岗位匹配|我更偏|偏好|更偏向|只推荐)", text))):
        intent = "base_match"
    elif switch:
        intent = "switch_company"
    elif re.search(r"简历", text) and re.search(r"补充|补传|新上传|差距|缺口|纳入", text) and (anchor or explicit_prep):
        intent = "add_resume"
    elif explicit_prep or len(fields) >= 2 or (fields and re.search(r"(?:目标公司|公司|岗位方向|招聘类型)\s*[:：]", text)):
        intent = "interview_prep"
    elif anchor and (fields or re.search(r"^继续[吧。！!\s]*$|^(?:继续|深入|再讲|展开|补充).*(?:报告|技术题|行为题|面试|岗位|差距)|刚才.*(?:报告|面试|岗位)|报告里|这份报告", text)):
        intent = "switch_company" if any(fields.get(key) and anchor.get(key) and fields[key] != anchor[key] for key in FIELDS) else "follow_up_deepen"
    else:
        return None
    return _finish(intent, fields, anchor, has_resume, has_base)


async def decide(text, *, anchor=None, has_resume=False, has_base=False, mode_hint=None, fallback=None):
    result = classify_rules(text, anchor=anchor, has_resume=has_resume, has_base=has_base, mode_hint=mode_hint)
    if result is not None:
        return result
    if fallback is not None:
        try:
            payload = await fallback(clean_text(text), dict(anchor or {}))
            if (
                isinstance(payload, dict)
                and type(payload.get("in_scope")) is bool
                and payload.get("intent") in INTENTS
                and isinstance(payload.get("missing_fields"), list)
                and all(value in LABELS for value in payload["missing_fields"])
                and payload["in_scope"] == (payload["intent"] != "off_topic")
            ):
                if not payload["in_scope"]:
                    return Decision(False, "off_topic", anchor=dict(anchor or {}), source="model")
                # Recompute fields/resources from the request; the model's claims
                # about attachments and its guessed anchor are not authoritative.
                return _finish(payload["intent"], extract_fields(clean_text(text)), anchor, has_resume, has_base, "model")
        except Exception:
            # Provider errors may include private input; never log exception text.
            pass
    return Decision(False, "off_topic", anchor=dict(anchor or {}), source="unresolved")


def fixed_reply(decision):
    if decision.source == "unresolved":
        return UNRESOLVED
    if not decision.in_scope:
        return OFF_TOPIC
    if decision.missing_fields:
        return "请补充：" + "、".join(LABELS[key] for key in decision.missing_fields) + "。"
    return ""
