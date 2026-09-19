from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.jobscout_base import JobBaseContext, JobScoutBaseError
from app.gateway.routers import jobscout

pytestmark = pytest.mark.asyncio


def _status(*, auth: str = "authenticated") -> SimpleNamespace:
    return SimpleNamespace(
        installed=True,
        app_configured=True,
        auth=SimpleNamespace(status=auth),
        cli=SimpleNamespace(available=True),
    )


async def test_base_context_rejects_an_unauthenticated_lark_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "user-1")
    monkeypatch.setattr(jobscout, "get_lark_integration_status", lambda *_args, **_kwargs: _status(auth="not_configured"))

    with pytest.raises(HTTPException) as exc_info:
        await jobscout.load_base_context.__wrapped__(
            body=jobscout.JobBaseContextRequest(url="https://example.feishu.cn/base/token"),
            request=None,
            config=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 409
    assert "重新连接" in exc_info.value.detail


async def test_base_context_uses_the_current_user_and_returns_only_bounded_context(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_load(**kwargs):
        seen.update(kwargs)
        return JobBaseContext(
            table_id="tblJobs",
            table_name="岗位库",
            view_id="vewPublic",
            fields=["公司", "岗位"],
            records=[{"record_id": "rec1", "公司": "示例科技", "岗位": "AI 产品经理"}],
            record_count=1,
            has_more=False,
            context_truncated=False,
        )

    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "user-1")
    monkeypatch.setattr(jobscout, "get_lark_integration_status", lambda *_args, **_kwargs: _status())
    monkeypatch.setattr(jobscout, "load_job_base_context", fake_load)

    response = await jobscout.load_base_context.__wrapped__(
        body=jobscout.JobBaseContextRequest(url="https://example.feishu.cn/base/token", limit=25),
        request=None,
        config=SimpleNamespace(),
    )

    assert seen == {
        "user_id": "user-1",
        "url": "https://example.feishu.cn/base/token",
        "limit": 25,
        "table_id": None,
    }
    assert response.record_count == 1
    assert response.records[0]["record_id"] == "rec1"


async def test_base_context_maps_safe_adapter_failures_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "user-1")
    monkeypatch.setattr(jobscout, "get_lark_integration_status", lambda *_args, **_kwargs: _status())
    monkeypatch.setattr(
        jobscout,
        "load_job_base_context",
        lambda **_kwargs: (_ for _ in ()).throw(JobScoutBaseError("当前账号无权访问该岗位表。")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await jobscout.load_base_context.__wrapped__(
            body=jobscout.JobBaseContextRequest(url="https://example.feishu.cn/base/token"),
            request=None,
            config=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "当前账号无权访问该岗位表。"
