"""Synthetic schemas, never copied applicant payloads or live API calls."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.application_tracker.adapters import try_adapters
from app.application_tracker.models import ApplicationInput, ApplicationStatus, CheckResult
from app.application_tracker.observations import captured_json_responses

FIXTURES = Path(__file__).parent / "fixtures/application_tracker/adapters"
NOW = datetime(2026, 10, 4, tzinfo=UTC)


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def run_response(response, *, role="待识别岗位", previous=None):
    app = ApplicationInput(company="Example", role=role, url=response["url"].split("/api/")[0])
    responses = captured_json_responses([response], page_url=app.url)
    return try_adapters(app, responses, checked_at=NOW, previous=previous)


@pytest.mark.parametrize("name,expected", [("feishu", ApplicationStatus.APPLIED), ("tencent", ApplicationStatus.RESUME_SCREENING), ("xiaohongshu", ApplicationStatus.TERMINATED)])
def test_known_schema_maps_current_status_and_verbatim_evidence(name, expected):
    response = fixture(name)
    result = run_response(response)
    assert result.record is not None and result.reason is None
    assert result.record.status is expected
    for item in result.record.discovered_applications:
        assert item.evidence in json.dumps(response["body"], ensure_ascii=False)
        assert item.raw_status in item.evidence
        assert item.role in item.role_evidence
    if name == "feishu":
        assert [(item.role, item.status) for item in result.record.discovered_applications] == [("示例开发岗位", ApplicationStatus.APPLIED), ("示例测试岗位", ApplicationStatus.TERMINATED)]
    if name == "xiaohongshu":
        assert result.record.applied_at.isoformat() == "2026-01-10"


@pytest.mark.parametrize("mutation", ["unknown_code", "bool_code", "login_error", "schema_drift", "duplicate_role", "too_many_rows", "empty", "truncated", "http_error", "foreign_host", "wrong_path"])
def test_feishu_falls_back_without_guessing_or_partial_listing(mutation):
    response = fixture("feishu")
    rows = response["body"]["data"]["delivery_list"]
    if mutation == "unknown_code":
        rows[1]["current_stage"]["stage_id"] = 2
    if mutation == "bool_code":
        rows[0]["current_stage"]["stage_id"] = False
    if mutation == "login_error":
        response["body"]["code"] = 401
    if mutation == "schema_drift":
        rows[0]["current_stage"] = {"new_status": 0}
    if mutation == "duplicate_role":
        rows[1]["job_post_info"]["title"] = rows[0]["job_post_info"]["title"]
    if mutation == "too_many_rows":
        rows.extend([deepcopy(rows[0])] * 100)
    if mutation == "empty":
        rows.clear()
    if mutation == "truncated":
        response["truncated"] = True
    if mutation == "http_error":
        response["status"] = 401
    if mutation == "foreign_host":
        response["url"] = response["url"].replace("jobs.bytedance.com", "jobs.bytedance.com.evil.example")
    if mutation == "wrong_path":
        response["url"] += "/recommendations"
    result = run_response(response)
    assert result.record is None and result.reason


def test_exact_role_can_match_known_row_but_not_borrow_another_rows_status():
    response = fixture("feishu")
    response["body"]["data"]["delivery_list"][1]["current_stage"]["stage_id"] = 2
    assert run_response(response, role="示例开发岗位").record.status is ApplicationStatus.APPLIED
    assert run_response(response, role="示例测试岗位").record is None
    assert run_response(response, role="示例").record is None


def test_latest_matching_response_is_authoritative_including_api_error():
    good = fixture("feishu")
    bad = deepcopy(good)
    bad["body"]["code"] = 401
    app = ApplicationInput(company="Example", role="待识别岗位", url="https://jobs.bytedance.com/applications")
    result = try_adapters(app, captured_json_responses([good, bad], page_url=app.url))
    assert result.record is None and result.reason == "api_error"


@pytest.mark.parametrize("name", ["tencent", "xiaohongshu"])
def test_unknown_current_state_and_api_errors_do_not_use_historical_steps(name):
    response = fixture(name)
    if name == "tencent":
        response["body"]["data"]["currentStatus"]["status"] = 999
    else:
        response["body"]["data"][0]["status"] = "offer"
    assert run_response(response).record is None
    response = fixture(name)
    response["body"]["status" if name == "tencent" else "statusCode"] = 401
    assert run_response(response).record is None


def test_xhs_requires_terminal_marker_and_rejects_future_date():
    response = fixture("xiaohongshu")
    response["body"]["data"][0]["applyTime"] = "2099-01-01"
    record = run_response(response).record
    assert record.applied_at is None and not record.applied_at_evidence
    response["body"]["data"][0]["steps"][-1]["stepStatus"] = "pending"
    assert run_response(response).record is None


def test_previous_change_time_is_preserved_only_for_same_status():
    response = fixture("tencent")
    old = run_response(response).record.model_copy(update={"changed_at": NOW - timedelta(days=4)})
    assert run_response(response, previous=old).record.changed_at == old.changed_at


def test_capture_envelopes_are_same_host_bounded_redacted_and_never_partially_parsed():
    response = fixture("feishu")
    response["body"]["token"] = "secret-value"
    response["url"] += "?token=private-value"
    result = captured_json_responses([response], page_url="https://jobs.bytedance.com/a")
    assert "secret-value" not in result[0].model_dump_json() and "private-value" not in result[0].url
    assert not captured_json_responses([response], page_url="https://other.example/a")
    response["body"]["oversized"] = "x" * 256_001
    assert run_response(response).record is None


@pytest.mark.parametrize(
    "host", ["jobs.bytedance.com", "xiaomi.jobs.f.mioffice.cn", "arashivision.jobs.feishu.cn", "campus.dewu.com", "wepie.jobs.feishu.cn", "xiaopeng.jobs.feishu.cn", "nio.jobs.feishu.cn", "campus.duxiaoman.com", "hf7l9aiqzx.jobs.feishu.cn"]
)
def test_feishu_verified_hosts_use_same_schema(host):
    response = fixture("feishu")
    response["url"] = response["url"].replace("jobs.bytedance.com", host)
    assert run_response(response).record is not None


@pytest.mark.parametrize("origin", ["http://jobs.bytedance.com", "https://jobs.bytedance.com:8443"])
def test_unverified_transport_cannot_claim_known_adapter(origin):
    response = fixture("feishu")
    response["url"] = response["url"].replace("https://jobs.bytedance.com", origin)
    assert run_response(response).record is None


@pytest.mark.asyncio
async def test_adapter_hit_never_creates_binds_or_calls_model(monkeypatch, tmp_path):
    from test_application_tracker_agent_workflow import FakeBrowser, FakeBrowserFactory

    from app.application_tracker.agent.workflow import ApplicationTrackerAgent
    from app.application_tracker.browser.models import BrowserAccessConfig, BrowserAccessResult, LoginState

    response = fixture("tencent")

    class Browser(FakeBrowser):
        async def open_page(self):
            return BrowserAccessResult(page_text="", check_result=CheckResult.SUCCESS, login_state=LoginState.AUTHENTICATED, json_responses=tuple(captured_json_responses([response], page_url=response["url"])))

    forbidden = Mock(side_effect=AssertionError("Model must stay uninitialized"))
    monkeypatch.setattr("deerflow.models.create_chat_model", forbidden)
    browser = Browser(page_text="")
    agent = ApplicationTrackerAgent.from_model_name(browser_factory=FakeBrowserFactory(browser))
    record = await agent.run(ApplicationInput(company="Example", role="待识别岗位", url="https://join.qq.com/a"), user_id="u", browser_config=BrowserAccessConfig(profile_root=tmp_path))
    assert record.status is ApplicationStatus.RESUME_SCREENING and browser.closed
    forbidden.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_adapter_status_lazily_uses_existing_grounded_extractor(monkeypatch, tmp_path):
    from deerflow.config.app_config import AppConfig

    monkeypatch.setattr("deerflow.config.get_app_config", lambda: AppConfig.model_validate({"sandbox": {"use": "fake:Sandbox"}}))
    from test_application_tracker_agent_workflow import FakeBrowser, FakeBrowserFactory
    from test_application_tracker_extractor import StubStructuredModel

    from app.application_tracker.agent.workflow import ApplicationTrackerAgent
    from app.application_tracker.browser.models import BrowserAccessConfig, BrowserAccessResult, LoginState

    response = fixture("tencent")
    response["body"]["data"]["currentStatus"]["status"] = 999

    class Browser(FakeBrowser):
        async def open_page(self):
            return BrowserAccessResult(page_text="示例开发岗位 当前状态：已投递", check_result=CheckResult.SUCCESS, login_state=LoginState.AUTHENTICATED, json_responses=tuple(captured_json_responses([response], page_url=response["url"])))

    model = Mock()
    model.with_structured_output.return_value = StubStructuredModel({"status": "已投递", "raw_status": "已投递", "evidence": "当前状态：已投递", "confidence": 0.01})
    create = Mock(return_value=model)
    monkeypatch.setattr("deerflow.models.create_chat_model", create)
    agent = ApplicationTrackerAgent.from_model_name(browser_factory=FakeBrowserFactory(Browser(page_text="")))
    create.assert_not_called()
    result = await agent.run(ApplicationInput(company="Example", role="示例开发岗位", url="https://join.qq.com/a"), user_id="u", browser_config=BrowserAccessConfig(profile_root=tmp_path))
    assert result.status is ApplicationStatus.APPLIED
    create.assert_called_once()
    model.bind_tools.assert_not_called()


@pytest.mark.asyncio
async def test_click_refreshes_envelopes_and_adapter_precedes_second_extraction(tmp_path):
    from test_application_tracker_agent_workflow import FakeBrowser, FakeBrowserFactory, StubExtractor, StubPlanner, _record

    from app.application_tracker.agent.workflow import ApplicationTrackerAgent
    from app.application_tracker.browser.models import BrowserAccessConfig

    response = fixture("tencent")

    class Browser(FakeBrowser):
        async def get_json_responses(self):
            return captured_json_responses([response], page_url=response["url"]) if self.clicks else []

    browser = Browser(page_text="Welcome", after_click_text="", element_name="我的投递")
    extractor = StubExtractor(_record(ApplicationStatus.UNKNOWN, confidence=0, raw_status="", evidence=""))
    planner = StubPlanner()
    agent = ApplicationTrackerAgent(model=planner, extractor=extractor, browser_factory=FakeBrowserFactory(browser))
    result = await agent.run(ApplicationInput(company="Example", role="待识别岗位", url="https://join.qq.com/a"), user_id="u", browser_config=BrowserAccessConfig(profile_root=tmp_path))
    assert result.status is ApplicationStatus.RESUME_SCREENING
    assert extractor.calls == 1 and not planner.calls and browser.closed


@pytest.mark.asyncio
async def test_tool_payload_excludes_full_adapter_envelope_and_clears_it_on_navigation():
    from test_application_tracker_agent_workflow import FakeBrowser

    from app.application_tracker.agent.tools import ApplicationTrackerToolbox
    from app.application_tracker.browser.models import BrowserAccessResult, LoginState

    response = fixture("tencent")
    response["body"]["data"]["privateNote"] = "never-send-to-model"

    class Browser(FakeBrowser):
        async def open_page(self):
            return BrowserAccessResult(page_text="Welcome", check_result=CheckResult.SUCCESS, login_state=LoginState.AUTHENTICATED, json_responses=tuple(captured_json_responses([response], page_url=response["url"])))

        async def get_json_responses(self):
            return ()

    toolbox = ApplicationTrackerToolbox(application=ApplicationInput(company="Example", role="待识别岗位", url="https://join.qq.com/a"), browser=Browser(page_text=""))
    payload = await toolbox.open_page()
    assert toolbox.json_responses and "never-send-to-model" not in payload
    await toolbox.click(ref=3)
    assert not toolbox.json_responses


@pytest.mark.asyncio
async def test_collector_preserves_http_error_to_prevent_reusing_previous_success():
    from types import SimpleNamespace

    from app.application_tracker.observations import JsonCollector

    response = fixture("tencent")

    async def body():
        return b'{"status": 401}'

    collector = JsonCollector(response["url"])
    collector.on_response(SimpleNamespace(url=response["url"], status=401, headers={"content-type": "application/json"}, request=SimpleNamespace(resource_type="xhr"), body=body))
    await collector.drain()
    assert collector.responses[0]["status"] == 401
    await collector.close()


def test_adapter_evaluation_counts_coverage_accuracy_fallback_and_zero_cost():
    from app.application_tracker.evaluation import EvaluationCase, evaluate_cases

    response = fixture("tencent")
    good = EvaluationCase(
        company="Example",
        role="待识别岗位",
        url=response["url"],
        case_id="known",
        page_text="",
        json_responses=captured_json_responses([response], page_url=response["url"]),
        expected_status=ApplicationStatus.RESUME_SCREENING,
        expected_role="示例开发岗位",
    )
    missing = good.model_copy(update={"case_id": "missing", "json_responses": []})
    report = evaluate_cases([good, missing], adapters_only=True)
    assert report.adapter_metrics.accepted == 1
    assert report.adapter_metrics.coverage == 0.5
    assert report.adapter_metrics.status_accuracy == 1
    assert report.adapter_metrics.fallback_reasons == {"no_matching_response": 1}
    assert report.verbatim_evidence_rate == 1
    assert report.total_input_tokens == report.total_output_tokens == report.total_cost_usd == 0
    assert report.unknown_count == 1


def test_evaluation_labels_are_never_adapter_input():
    from app.application_tracker.evaluation import EvaluationCase, evaluate_cases

    response = fixture("feishu")
    response["body"]["data"]["delivery_list"][1]["current_stage"]["stage_id"] = 2
    case = EvaluationCase(
        company="Example",
        role="待识别岗位",
        url=response["url"],
        case_id="mixed",
        page_text="",
        json_responses=captured_json_responses([response], page_url=response["url"]),
        expected_status=ApplicationStatus.APPLIED,
        expected_role="示例开发岗位",
    )
    report = evaluate_cases([case], adapters_only=True)
    assert report.adapter_metrics.accepted == 0 and report.unknown_count == 1
    assert report.adapter_metrics.fallback_reasons == {"unknown_status": 1}


def test_adapter_cli_restricts_private_paths_before_reading(monkeypatch, tmp_path):
    from scripts import application_tracker_adapters_eval as cli

    monkeypatch.setattr(cli, "PRIVATE_ROOT", tmp_path / "private")
    with pytest.raises(SystemExit, match="local_eval"):
        cli.main(["--dataset", str(tmp_path / "outside.jsonl")])
