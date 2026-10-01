import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application_tracker.browser.models import BrowserAccessConfig, LoginState
from app.application_tracker.snapshot import (
    SnapshotLoginRequired,
    capture_snapshot,
    redact_json,
    sanitize_url,
)


class FakeResponse:
    def __init__(self, url: str, body: bytes, *, content_length: str | None = None) -> None:
        self.url = url
        self.status = 200
        self.request = SimpleNamespace(resource_type="xhr")
        self.headers = {"content-type": "application/json"}
        if content_length is not None:
            self.headers["content-length"] = content_length
        self.payload = body
        self.body_called = False

    async def body(self) -> bytes:
        self.body_called = True
        return self.payload


class FakePage:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.url = "https://careers.example.com/applications/123456?session=private"
        self.responses = responses
        self.handler = None

    async def goto(self, *_args, **_kwargs) -> None:
        for response in self.responses:
            self.handler(response)

    async def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None

    async def wait_for_timeout(self, *_args, **_kwargs) -> None:
        return None

    def locator(self, selector: str):
        assert selector == "body"
        return self

    async def inner_text(self, **_kwargs) -> str:
        return "岗位：AI 产品经理\n当前状态：测评"

    async def screenshot(self, **_kwargs) -> bytes:
        return b"synthetic-png"


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.pages = [page]
        self.closed = False

    def on(self, event: str, handler) -> None:
        assert event == "response"
        self.pages[0].handler = handler

    async def route(self, *_args, **_kwargs) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class FakeLauncher:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.profile_dir = None

    async def launch(self, profile_dir: Path, **_kwargs) -> FakeContext:
        self.profile_dir = profile_dir
        return self.context


def test_redaction_keeps_response_shape_and_masks_common_identifiers() -> None:
    source = {
        "status": "测评",
        "candidateId": "abc-123",
        "contact": ["me@example.com", "13800138000", "Bearer secret-value"],
        "detailUrl": "https://careers.example.com/applications/1?token=secret",
    }
    assert redact_json(source) == {
        "status": "测评",
        "candidateId": "[REDACTED]",
        "contact": ["[REDACTED]", "[REDACTED]", "[REDACTED]"],
        "detailUrl": "https://careers.example.com/applications/[REDACTED]?token=%5BREDACTED%5D",
    }
    assert sanitize_url("https://careers.example.com/applications/123456?token=secret#fragment") == ("https://careers.example.com/applications/[REDACTED]?token=%5BREDACTED%5D")
    assert sanitize_url("https://example.com:8443/applications/1") == "https://example.com:8443/applications/[REDACTED]"


@pytest.mark.asyncio
async def test_capture_saves_private_body_bounded_json_final_url_and_screenshot(tmp_path: Path, monkeypatch) -> None:
    async def allow_url(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.application_tracker.snapshot.validate_navigation_url", allow_url)
    good = FakeResponse(
        "https://careers.example.com/api/applications/123456?token=private",
        json.dumps({"status": "测评", "email": "me@example.com"}).encode(),
    )
    large = FakeResponse("https://careers.example.com/api/large", b"{}", content_length="999999")
    page = FakePage([good, large])
    context = FakeContext(page)
    launcher = FakeLauncher(context)
    detector = SimpleNamespace(inspect=lambda _page: _authenticated())
    destination = await capture_snapshot(
        "https://careers.example.com/applications/123456",
        user_id="synthetic-user",
        output_root=tmp_path / "snapshots",
        config=BrowserAccessConfig(profile_root=tmp_path / "profiles"),
        launcher=launcher,
        detector=detector,
    )

    metadata = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
    responses = json.loads((destination / "responses.json").read_text(encoding="utf-8"))
    assert metadata["final_url"] == "https://careers.example.com/applications/[REDACTED]?session=%5BREDACTED%5D"
    assert metadata["json_response_count"] == 2
    assert (destination / "body.txt").read_text(encoding="utf-8") == "岗位：AI 产品经理\n当前状态：测评"
    assert (destination / "screenshot.png").read_bytes() == b"synthetic-png"
    assert responses[0]["body"] == {"status": "测评", "email": "[REDACTED]"}
    assert responses[1]["truncated"] is True
    assert large.body_called is False
    assert context.closed is True
    assert launcher.profile_dir.parent.parent == tmp_path / "profiles"


async def _authenticated():
    return SimpleNamespace(state=LoginState.AUTHENTICATED)


@pytest.mark.asyncio
async def test_capture_requires_existing_login_and_leaves_no_snapshot(tmp_path: Path, monkeypatch) -> None:
    async def allow_url(*_args, **_kwargs):
        return None

    async def login_required(_page):
        return SimpleNamespace(state=LoginState.LOGIN_REQUIRED)

    monkeypatch.setattr("app.application_tracker.snapshot.validate_navigation_url", allow_url)
    context = FakeContext(FakePage([]))
    with pytest.raises(SnapshotLoginRequired):
        await capture_snapshot(
            "https://careers.example.com/applications/1",
            user_id="synthetic-user",
            output_root=tmp_path / "snapshots",
            config=BrowserAccessConfig(profile_root=tmp_path / "profiles"),
            launcher=FakeLauncher(context),
            detector=SimpleNamespace(inspect=login_required),
        )
    assert not (tmp_path / "snapshots").exists()
    assert context.closed is True


@pytest.mark.asyncio
async def test_capture_limits_number_of_json_responses(tmp_path: Path, monkeypatch) -> None:
    async def allow_url(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.application_tracker.snapshot.validate_navigation_url", allow_url)
    page = FakePage(
        [
            FakeResponse("https://careers.example.com/api/one", b'{"status":"one"}'),
            FakeResponse("https://careers.example.com/api/two", b'{"status":"two"}'),
        ]
    )
    destination = await capture_snapshot(
        "https://careers.example.com/applications/1",
        user_id="synthetic-user",
        output_root=tmp_path / "snapshots",
        config=BrowserAccessConfig(profile_root=tmp_path / "profiles"),
        launcher=FakeLauncher(FakeContext(page)),
        detector=SimpleNamespace(inspect=lambda _page: _authenticated()),
        max_json_responses=1,
    )
    metadata = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
    responses = json.loads((destination / "responses.json").read_text(encoding="utf-8"))
    assert metadata["json_response_count"] == 1
    assert metadata["omitted_json_responses"] == 1
    assert responses[0]["body"] == {"status": "one"}
