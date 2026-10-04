"""Synthetic stage-4 evidence; no account, network or model access."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.application_tracker.confidence import assess_status
from app.application_tracker.models import ApplicationStatus
from app.application_tracker.observations import JsonCollector, SourceObservation, json_observations


def test_json_records_stay_separate_and_private_fields_are_removed():
    sources = json_observations(
        [
            {
                "url": "https://jobs.example/api",
                "status": 200,
                "body": {
                    "applications": [
                        {"role": "产品经理", "status": "已投递", "token": "secret", "email": "private@example.com"},
                        {"role": "开发工程师", "status": "未通过"},
                    ]
                },
            }
        ],
        page_url="https://jobs.example/applications",
    )
    assert len(sources) == 2
    assert "secret" not in sources[0].text
    assert "private@example.com" not in sources[0].text
    assert "开发工程师" not in sources[0].text


@pytest.mark.parametrize("status,url", [(401, "https://jobs.example/api"), (200, "https://evil.example/api")])
def test_error_and_foreign_responses_are_not_evidence(status, url):
    assert not json_observations([{"url": url, "status": status, "body": {"role": "产品经理", "status": "已投递"}}], page_url="https://jobs.example/applications")


def test_confidence_requires_matched_status_and_same_record():
    text = "role: 产品经理\nstatus: 已投递"
    source = SourceObservation(kind="json", text=text)
    good = assess_status(ApplicationStatus.APPLIED, raw_status="已投递", evidence="status: 已投递", role="产品经理", sources=[source])
    assert good.status is ApplicationStatus.APPLIED and good.confidence >= 0.7
    wrong = assess_status(ApplicationStatus.OFFER, raw_status="已投递", evidence="status: 已投递", role="产品经理", sources=[source])
    assert wrong.status is ApplicationStatus.UNKNOWN and wrong.confidence == 0
    cross = assess_status(ApplicationStatus.APPLIED, raw_status="已投递", evidence="status: 已投递", role="开发工程师", sources=[source])
    assert cross.status is ApplicationStatus.UNKNOWN


def test_conflicting_sources_do_not_produce_fast_path():
    sources = [SourceObservation(kind="json", text="role: 产品经理\nstatus: 已投递"), SourceObservation(kind="dom", text="产品经理 当前状态：未通过")]
    result = assess_status(ApplicationStatus.APPLIED, raw_status="已投递", evidence="status: 已投递", role="产品经理", sources=sources)
    assert result.status is ApplicationStatus.UNKNOWN


@pytest.mark.asyncio
async def test_collector_caps_count_and_size_and_drains_tasks():
    calls = 0

    async def body():
        nonlocal calls
        calls += 1
        return json.dumps({"role": "产品经理", "status": "已投递"}).encode()

    collector = JsonCollector("https://jobs.example/a", max_responses=1, max_json_bytes=100)
    response = SimpleNamespace(url="https://jobs.example/api", status=200, headers={"content-type": "application/json", "content-length": "1000"}, request=SimpleNamespace(resource_type="xhr"), body=body)
    collector.on_response(response)
    collector.on_response(response)
    await collector.drain()
    assert calls == 0
    assert len(collector.responses) == 1 and collector.responses[0]["truncated"]
    await collector.close()


@pytest.mark.asyncio
async def test_collector_cancellation_cleans_pending_body():
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def body():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    collector = JsonCollector("https://jobs.example/a")
    collector.on_response(SimpleNamespace(url="https://jobs.example/api", status=200, headers={"content-type": "application/json"}, request=SimpleNamespace(resource_type="fetch"), body=body))
    await started.wait()
    await collector.close()
    assert cancelled.is_set()


def test_nested_noise_and_oversized_records_are_bounded():
    body = {"applications": [{"role": "产品经理", "status": "已投递", "description": "x" * 300_000, "recommendations": [{"role": "推荐岗位", "status": "Offer"}]}]}
    sources = json_observations([{"url": "https://jobs.example/api", "status": 200, "body": body}], page_url="https://jobs.example/a", max_chars=500)
    assert len(sources) == 1 and len(sources[0].text) < 500
    assert "推荐岗位" not in sources[0].text


def test_model_confidence_does_not_change_extractor_decision():
    from test_application_tracker_extractor import StubStructuredModel

    from app.application_tracker.extractor import StatusExtractor
    from app.application_tracker.models import ApplicationInput

    application = ApplicationInput(company="Example", role="产品经理", url="https://jobs.example/a")
    scores = []
    for confidence in [0.01, 0.99]:
        model = StubStructuredModel({"status": "已投递", "raw_status": "已投递", "evidence": "产品经理 已投递", "confidence": confidence})
        scores.append(StatusExtractor(model).extract(application, "产品经理 已投递").confidence)
    assert scores[0] == scores[1] and scores[0] >= 0.7


def test_json_primary_and_invalid_json_falls_back_to_dom():
    from app.application_tracker.extractor import StatusExtractor
    from app.application_tracker.models import ApplicationInput

    application = ApplicationInput(company="Example", role="产品经理", url="https://jobs.example/a")
    source = SourceObservation(kind="json", text="role: 产品经理\nstatus: 已投递")

    class Model:
        def __init__(self, bad=False):
            self.calls, self.bad = [], bad

        def invoke(self, messages):
            self.calls.append(messages)
            if self.bad and len(self.calls) == 1:
                return {"status": "Offer", "raw_status": "不存在", "evidence": "不存在", "confidence": 0.99}
            return {"status": "已投递", "raw_status": "已投递", "evidence": "已投递", "confidence": 0.01}

    model = Model()
    record = StatusExtractor(model).extract(application, "空白应用外壳", observations=[source])
    assert record.status is ApplicationStatus.APPLIED and record.confidence >= 0.7
    assert len(model.calls) == 1 and "role: 产品经理" in str(model.calls[0][1].content)
    model = Model(bad=True)
    record = StatusExtractor(model).extract(application, "产品经理 已投递", observations=[source])
    assert record.status is ApplicationStatus.APPLIED and len(model.calls) == 2


def test_cross_row_status_and_date_cannot_be_borrowed():
    from test_application_tracker_extractor import StubStructuredModel

    from app.application_tracker.extractor import StatusExtractor
    from app.application_tracker.models import ApplicationInput

    source = "岗位：产品经理\n状态：已投递\n\n岗位：开发工程师\n状态：未通过\n投递时间：2026-01-01"
    response = {
        "status": "未知",
        "confidence": 0.99,
        "discovered_applications": [
            {"role": "产品经理", "role_evidence": "岗位：产品经理", "status": "未通过", "raw_status": "未通过", "evidence": "状态：未通过", "confidence": 0.99, "applied_at": "2026-01-01", "applied_at_evidence": "投递时间：2026-01-01"},
            {"role": "开发工程师", "role_evidence": "岗位：开发工程师", "status": "未通过", "raw_status": "未通过", "evidence": "状态：未通过", "confidence": 0.99},
        ],
    }
    record = StatusExtractor(StubStructuredModel(response)).extract(ApplicationInput(company="Example", role="待识别岗位", url="https://jobs.example/a"), source)
    assert [row.role for row in record.discovered_applications] == ["开发工程师"]


@pytest.mark.asyncio
async def test_body_stability_wins_and_cancels_never_idle(monkeypatch, tmp_path):
    from app.application_tracker.browser import observation
    from app.application_tracker.browser.models import BrowserAccessConfig

    cancelled = asyncio.Event()

    class Page:
        async def wait_for_load_state(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        def locator(self, _):
            return self

        async def inner_text(self, **kwargs):
            return "产品经理 已投递"

    original_sleep = asyncio.sleep

    async def tick(_):
        await original_sleep(0)

    monkeypatch.setattr(observation.asyncio, "sleep", tick)
    await observation.wait_for_observation(Page(), BrowserAccessConfig(profile_root=tmp_path))
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_never_ready_page_is_bounded_and_probes_are_cancelled(tmp_path):
    from app.application_tracker.browser.models import BrowserAccessConfig
    from app.application_tracker.browser.observation import wait_for_observation

    cancelled = []

    class Page:
        async def wait_for_load_state(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append("idle")

        def locator(self, _):
            return self

        async def inner_text(self, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append("body")

    await asyncio.wait_for(wait_for_observation(Page(), BrowserAccessConfig(profile_root=tmp_path, settle_timeout_ms=20, stable_poll_ms=5)), 1)
    assert set(cancelled) == {"idle", "body"}
