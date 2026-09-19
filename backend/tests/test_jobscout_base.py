"""JobScout's read-only Feishu Base context adapter."""

import json
from pathlib import Path

import pytest

from app.gateway.jobscout_base import (
    JobScoutBaseError,
    load_job_base_context,
    select_job_fields,
    validate_base_url,
)


def test_validate_base_url_accepts_feishu_and_rejects_lookalike_hosts() -> None:
    assert validate_base_url("https://example.feishu.cn/base/bascn123?table=tblJobs") == (
        "https://example.feishu.cn/base/bascn123?table=tblJobs"
    )
    assert validate_base_url("https://example.larksuite.com/wiki/wikcn123") == "https://example.larksuite.com/wiki/wikcn123"

    with pytest.raises(JobScoutBaseError, match="飞书"):
        validate_base_url("https://example.feishu.cn.evil.test/base/bascn123")
    with pytest.raises(JobScoutBaseError, match="HTTPS"):
        validate_base_url("http://example.feishu.cn/base/bascn123")


def test_select_job_fields_prioritizes_job_semantics_and_primary_field() -> None:
    fields = [
        {"field_id": "fldPrimary", "field_name": "职位名称", "is_primary": True},
        {"field_id": "fldCompany", "field_name": "公司"},
        {"field_id": "fldRequirements", "field_name": "任职要求"},
        {"field_id": "fldLocation", "field_name": "工作地点"},
        {"field_id": "fldStatus", "field_name": "招聘状态"},
        {"field_id": "fldPrivate", "field_name": "内部备注"},
        {"field_id": "fldPhone", "field_name": "招聘联系人手机"},
    ]

    selected = select_job_fields(fields, max_fields=5)

    assert [field.name for field in selected] == ["职位名称", "公司", "任职要求", "工作地点", "招聘状态"]
    assert all(field.field_id != "fldPrivate" for field in selected)
    assert all(field.field_id != "fldPhone" for field in selected)


def test_select_job_fields_does_not_fall_back_to_unrelated_private_columns() -> None:
    fields = [
        {"field_id": "fldName", "field_name": "候选人姓名", "is_primary": True},
        {"field_id": "fldPhone", "field_name": "手机号码"},
        {"field_id": "fldNotes", "field_name": "内部备注"},
    ]

    assert select_job_fields(fields) == []


def test_load_job_base_context_uses_only_read_commands_and_bounds_model_context(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_runner(args: list[str], cwd: Path | None) -> dict[str, object]:
        commands.append(args)
        command = args[1]
        if command == "+url-resolve":
            return {
                "ok": True,
                "data": {
                    "resource_type": "bitable",
                    "base_token": "basToken",
                    "table_id": "tblJobs",
                    "view_id": "vewOpen",
                },
            }
        if command == "+table-list":
            return {
                "ok": True,
                "data": {
                    "items": [
                        {"table_id": "tblArchive", "name": "历史归档"},
                        {"table_id": "tblJobs", "name": "2027 校招岗位"},
                    ]
                },
            }
        if command == "+field-list":
            return {
                "ok": True,
                "data": {
                    "items": [
                        {"field_id": "fldRole", "field_name": "岗位名称", "is_primary": True},
                        {"field_id": "fldCompany", "field_name": "公司"},
                        {"field_id": "fldRequirements", "field_name": "任职要求"},
                        {"field_id": "fldLink", "field_name": "投递链接"},
                        {"field_id": "fldPrivate", "field_name": "内部备注"},
                    ]
                },
            }
        if command == "+record-list":
            assert cwd is not None
            output_name = args[args.index("--output") + 1]
            output = cwd / output_name
            rows = [
                {
                    "record_id": "rec1",
                    "fields": {
                        "岗位名称": "AI 产品经理",
                        "公司": "示例科技",
                        "任职要求": "熟悉 Agent、RAG 与 SQL",
                        "投递链接": "https://jobs.example.cn/1",
                        "内部备注": "不应进入模型上下文",
                    },
                },
                {
                    "record_id": "rec2",
                    "fields": {
                        "岗位名称": "后端开发",
                        "公司": "示例科技",
                        "任职要求": "Python 与 PostgreSQL" + "很长" * 1000,
                    },
                },
            ]
            output.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
            return {"ok": True, "data": {"records_count": 2, "has_more": False}}
        raise AssertionError(f"Unexpected command: {args}")

    context = load_job_base_context(
        user_id="user-1",
        url="https://example.feishu.cn/base/bascn123?table=tblJobs&view=vewOpen",
        limit=50,
        runner=fake_runner,
        temp_root=tmp_path,
    )

    assert context.table_id == "tblJobs"
    assert context.table_name == "2027 校招岗位"
    assert context.view_id == "vewOpen"
    assert context.record_count == 2
    assert context.has_more is False
    assert context.fields == ["岗位名称", "公司", "任职要求", "投递链接"]
    assert context.records[0]["record_id"] == "rec1"
    assert "内部备注" not in context.records[0]
    assert len(str(context.records[1]["任职要求"])) <= 603

    assert [command[1] for command in commands] == ["+url-resolve", "+table-list", "+field-list", "+record-list"]
    assert all(command[0] == "base" and command[-2:] == ["--as", "user"] for command in commands)
    record_command = commands[-1]
    assert "--view-id" in record_command and "vewOpen" in record_command
    assert "fldPrivate" not in record_command
