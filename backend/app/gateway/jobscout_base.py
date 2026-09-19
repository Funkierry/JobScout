"""Read-only Feishu Base adapter for JobScout resume-to-role matching.

The Agent runtime may deliberately have host ``bash`` disabled.  This adapter
keeps the Lark CLI invocation on the authenticated Gateway side and exposes
only the four read commands needed to build a bounded, model-facing job
context.  It never accepts an arbitrary CLI command from the browser.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from deerflow.integrations.lark_cli import lark_cli_env, probe_lark_cli

MAX_BASE_URL_LENGTH = 2048
MAX_PROJECTED_FIELDS = 14
MAX_CELL_CHARS = 600
MAX_CONTEXT_CHARS = 60_000
MAX_NDJSON_BYTES = 10 * 1024 * 1024

_ALLOWED_BASE_HOSTS = ("feishu.cn", "larksuite.com", "larkoffice.com")
_TABLE_ID_RE = re.compile(r"^tbl[A-Za-z0-9_-]{2,128}$")
_JOB_FIELD_KEYWORDS = (
    "岗位",
    "职位",
    "职务",
    "公司",
    "企业",
    "部门",
    "业务",
    "方向",
    "类别",
    "类型",
    "招聘",
    "职责",
    "要求",
    "资格",
    "技能",
    "地点",
    "城市",
    "地区",
    "学历",
    "专业",
    "经验",
    "年限",
    "薪资",
    "状态",
    "截止",
    "时间",
    "链接",
    "地址",
    "投递",
    "job",
    "role",
    "position",
    "company",
    "department",
    "location",
    "city",
    "requirement",
    "qualification",
    "responsibility",
    "skill",
    "status",
    "deadline",
    "apply",
    "url",
    "link",
)
_PRIVATE_FIELD_KEYWORDS = (
    "内部",
    "备注",
    "候选人",
    "姓名",
    "手机",
    "电话",
    "邮箱",
    "身份证",
    "微信",
    "住址",
    "面试评价",
    "联系人",
    "email",
    "phone",
    "mobile",
    "candidate",
    "contact",
    "note",
)


class JobScoutBaseError(RuntimeError):
    """Safe, user-displayable failure while preparing Base context."""


@dataclass(frozen=True)
class JobField:
    field_id: str
    name: str
    is_primary: bool = False


@dataclass(frozen=True)
class JobBaseContext:
    table_id: str
    table_name: str
    view_id: str | None
    fields: list[str]
    records: list[dict[str, Any]]
    record_count: int
    has_more: bool
    context_truncated: bool


CliRunner = Callable[[list[str], Path | None], dict[str, Any]]


def validate_base_url(raw_url: str) -> str:
    """Accept only HTTPS Feishu/Lark Base or wiki links."""

    url = str(raw_url or "").strip()
    if not url or len(url) > MAX_BASE_URL_LENGTH:
        raise JobScoutBaseError("请提供有效的飞书多维表格链接。")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise JobScoutBaseError("飞书多维表格链接必须使用 HTTPS。")
    if parsed.username or parsed.password:
        raise JobScoutBaseError("飞书多维表格链接不能包含登录凭据。")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in _ALLOWED_BASE_HOSTS):
        raise JobScoutBaseError("仅支持飞书或 Lark 官方域名下的多维表格链接。")
    if not any(segment in parsed.path.lower() for segment in ("/base/", "/wiki/")):
        raise JobScoutBaseError("该链接不是可识别的飞书 Base 或 Base Wiki 链接。")
    return url


def _field_from_raw(raw: dict[str, Any]) -> JobField | None:
    field_id = str(raw.get("field_id") or raw.get("id") or "").strip()
    name = str(raw.get("field_name") or raw.get("name") or raw.get("title") or "").strip()
    if not field_id or not name:
        return None
    return JobField(field_id=field_id, name=name, is_primary=bool(raw.get("is_primary") or raw.get("primary")))


def select_job_fields(raw_fields: list[dict[str, Any]], *, max_fields: int = MAX_PROJECTED_FIELDS) -> list[JobField]:
    """Project job-relevant columns while preserving the Base's display order."""

    fields = [field for raw in raw_fields if (field := _field_from_raw(raw)) is not None]
    if not fields:
        return []
    selected = []
    for field in fields:
        lowered = field.name.lower()
        if any(keyword in lowered for keyword in _PRIVATE_FIELD_KEYWORDS):
            continue
        if field.is_primary or any(keyword in lowered for keyword in _JOB_FIELD_KEYWORDS):
            selected.append(field)
    return selected[: max(1, max_fields)]


def _deep_first(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, ""):
                return candidate
        for child in value.values():
            candidate = _deep_first(child, keys)
            if candidate not in (None, ""):
                return candidate
    elif isinstance(value, list):
        for child in value:
            candidate = _deep_first(child, keys)
            if candidate not in (None, ""):
                return candidate
    return None


def _find_dict_list(value: Any, preferred_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in preferred_keys:
            candidate = value.get(key)
            if isinstance(candidate, list) and all(isinstance(item, dict) for item in candidate):
                return candidate
        for child in value.values():
            candidate = _find_dict_list(child, preferred_keys)
            if candidate:
                return candidate
    elif isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            return value
        for child in value:
            candidate = _find_dict_list(child, preferred_keys)
            if candidate:
                return candidate
    return []


def _payload_data(payload: dict[str, Any]) -> Any:
    if payload.get("ok") is False:
        raise JobScoutBaseError(_safe_cli_error(json.dumps(payload, ensure_ascii=False)))
    return payload.get("data", payload)


def _safe_cli_error(raw: str) -> str:
    normalized = raw.lower()
    if any(token in normalized for token in ("unauthorized", "not authorized", "token expired", "auth")):
        return "飞书授权已失效，请在 Capability Center 重新连接后再试。"
    if any(token in normalized for token in ("permission", "forbidden", "scope", "access denied")):
        return "当前飞书账号没有读取该多维表格的权限，请检查共享权限或授权范围。"
    if any(token in normalized for token in ("not found", "404", "invalid url", "invalid token")):
        return "未找到该多维表格，请确认链接仍然有效且当前账号可以访问。"
    return "读取飞书多维表格失败，请确认链接、登录状态和表格权限后重试。"


def _run_lark_cli_json(user_id: str, args: list[str], cwd: Path | None) -> dict[str, Any]:
    probe = probe_lark_cli()
    if not probe.available or not probe.path:
        raise JobScoutBaseError("Gateway 尚未安装可用的 Lark CLI。")
    try:
        result = subprocess.run(
            [probe.path, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
            cwd=str(cwd) if cwd else None,
            env=lark_cli_env(user_id),
        )
    except subprocess.TimeoutExpired as exc:
        raise JobScoutBaseError("读取飞书多维表格超时，请稍后重试。") from exc
    except OSError as exc:
        raise JobScoutBaseError("Gateway 无法启动 Lark CLI。") from exc

    raw = (result.stdout or result.stderr or "").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JobScoutBaseError(_safe_cli_error(raw)) from exc
    if not isinstance(payload, dict) or result.returncode != 0 or payload.get("ok") is False:
        raise JobScoutBaseError(_safe_cli_error(raw))
    return payload


def _table_identity(raw: dict[str, Any]) -> tuple[str, str]:
    table_id = str(raw.get("table_id") or raw.get("id") or raw.get("block_id") or "").strip()
    name = str(raw.get("name") or raw.get("table_name") or raw.get("title") or table_id).strip()
    return table_id, name


def _choose_table(raw_tables: list[dict[str, Any]], requested_table_id: str | None) -> tuple[str, str]:
    tables = [identity for raw in raw_tables if (identity := _table_identity(raw))[0]]
    if requested_table_id:
        for table_id, name in tables:
            if table_id == requested_table_id:
                return table_id, name
        raise JobScoutBaseError("链接指定的岗位表不存在或当前账号无权访问。")
    if not tables:
        raise JobScoutBaseError("该 Base 中没有可读取的数据表。")
    for table_id, name in tables:
        lowered = name.lower()
        if any(keyword in lowered for keyword in ("岗位", "职位", "招聘", "job", "role", "position")):
            return table_id, name
    return tables[0]


def _compact_cell(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_CELL_CHARS else value[:MAX_CELL_CHARS] + "..."
    if isinstance(value, list):
        rendered = "；".join(str(_compact_cell(item)) for item in value[:20])
        return rendered if len(rendered) <= MAX_CELL_CHARS else rendered[:MAX_CELL_CHARS] + "..."
    if isinstance(value, dict):
        for key in ("text", "name", "title", "url", "link"):
            if key in value and value[key] not in (None, ""):
                return _compact_cell(value[key])
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        return rendered if len(rendered) <= MAX_CELL_CHARS else rendered[:MAX_CELL_CHARS] + "..."
    return _compact_cell(str(value))


def _normalize_record(raw: dict[str, Any], fields: list[JobField]) -> dict[str, Any]:
    values = raw.get("fields") if isinstance(raw.get("fields"), dict) else raw
    record: dict[str, Any] = {}
    record_id = raw.get("record_id") or raw.get("id")
    if record_id:
        record["record_id"] = str(record_id)
    for field in fields:
        value = values.get(field.name, values.get(field.field_id))
        if value not in (None, "", []):
            record[field.name] = _compact_cell(value)
    return record


def _read_bounded_records(path: Path, fields: list[JobField]) -> tuple[list[dict[str, Any]], bool]:
    if not path.is_file() or path.stat().st_size > MAX_NDJSON_BYTES:
        raise JobScoutBaseError("飞书岗位数据文件缺失或超过安全读取上限。")
    records: list[dict[str, Any]] = []
    used_chars = 0
    truncated = False
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JobScoutBaseError("飞书岗位数据返回格式异常。") from exc
            if not isinstance(raw, dict):
                continue
            record = _normalize_record(raw, fields)
            serialized_size = len(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            if records and used_chars + serialized_size > MAX_CONTEXT_CHARS:
                truncated = True
                break
            records.append(record)
            used_chars += serialized_size
    return records, truncated


def load_job_base_context(
    *,
    user_id: str,
    url: str,
    limit: int = 200,
    table_id: str | None = None,
    runner: CliRunner | None = None,
    temp_root: Path | None = None,
) -> JobBaseContext:
    """Resolve one Base table and return a bounded read-only job context."""

    safe_url = validate_base_url(url)
    if table_id is not None and not _TABLE_ID_RE.fullmatch(table_id):
        raise JobScoutBaseError("table_id 格式无效。")
    bounded_limit = max(1, min(int(limit), 200))
    invoke = runner or (lambda args, cwd: _run_lark_cli_json(user_id, args, cwd))

    resolved = _payload_data(invoke(["base", "+url-resolve", "--url", safe_url, "--format", "json", "--as", "user"], None))
    base_token = str(_deep_first(resolved, ("base_token", "baseToken")) or "").strip()
    resolved_table_id = str(_deep_first(resolved, ("table_id", "tableId")) or "").strip() or None
    view_id = str(_deep_first(resolved, ("view_id", "viewId")) or "").strip() or None
    if not base_token:
        raise JobScoutBaseError("该链接未解析到普通飞书 Base；暂不支持 BaseApp 页面。")

    requested_table_id = table_id or resolved_table_id
    tables_payload = _payload_data(
        invoke(["base", "+table-list", "--base-token", base_token, "--format", "json", "--as", "user"], None)
    )
    raw_tables = _find_dict_list(tables_payload, ("items", "tables", "table_list"))
    selected_table_id, table_name = _choose_table(raw_tables, requested_table_id)

    fields_payload = _payload_data(
        invoke(
            [
                "base",
                "+field-list",
                "--base-token",
                base_token,
                "--table-id",
                selected_table_id,
                "--format",
                "json",
                "--as",
                "user",
            ],
            None,
        )
    )
    raw_fields = _find_dict_list(fields_payload, ("items", "fields", "field_list"))
    fields = select_job_fields(raw_fields)
    if not fields:
        raise JobScoutBaseError("岗位表没有可读取的字段。")

    temp_parent = str(temp_root) if temp_root else None
    with tempfile.TemporaryDirectory(prefix="jobscout-base-", dir=temp_parent) as temp_dir:
        workdir = Path(temp_dir).resolve()
        output_name = "records.ndjson"
        command = [
            "base",
            "+record-list",
            "--base-token",
            base_token,
            "--table-id",
            selected_table_id,
        ]
        for field in fields:
            command.extend(["--field-id", field.field_id])
        if view_id:
            command.extend(["--view-id", view_id])
        command.extend(["--limit", str(bounded_limit), "--format", "ndjson", "--output", output_name, "--as", "user"])
        summary = _payload_data(invoke(command, workdir))
        records, context_truncated = _read_bounded_records(workdir / output_name, fields)

    has_more = bool(_deep_first(summary, ("has_more", "hasMore")))
    return JobBaseContext(
        table_id=selected_table_id,
        table_name=table_name,
        view_id=view_id,
        fields=[field.name for field in fields],
        records=records,
        record_count=len(records),
        has_more=has_more,
        context_truncated=context_truncated,
    )
