import json
import unittest
from pathlib import Path

from evaluate_guardrail_fixtures import evaluate
from grade_runs import check_base_report, check_followup_gap, check_structured_evidence, replay_structured_report

FENCE = chr(96) * 3


def block(name, data):
    return FENCE + name + "\n" + json.dumps(data, ensure_ascii=False) + "\n" + FENCE


class StructuredEvidenceTest(unittest.TestCase):
    def test_v2_followup_allows_current_run_source_revalidation(self):
        run = {"output": "差距分析", "tool_calls": [{"name": "read_file", "status": "success"}, {"name": "web_fetch", "status": "success"}],
               "runtime": {"evidence_version": 2}}
        self.assertTrue(all(gate.status == "pass" for gate in check_followup_gap(run, {})))
        run["runtime"] = {}
        self.assertTrue(any(gate.gate_id == "reuse_existing_research" and gate.status == "fail" for gate in check_followup_gap(run, {})))

    def test_published_guardrail_fixtures_match_expected_behavior(self):
        result = evaluate(Path(__file__).resolve().parents[1] / "evals/fixtures/structured_guardrails.json")
        self.assertEqual(result["expectations_met"], result["fixture_count"])

    def run_fixture(self):
        claim = {"section": "business", "claim": "合成业务", "url": "https://example.cn/a", "quote": "数据服务"}
        return {
            "structured_evidence": {"server_date": "2026-10-03", "model_output": block("jobscout_report", {"company": "示例", "role": "开发", "evidence": [claim]})},
            "tool_messages": [
                {"type": "ai", "tool_calls": [{"id": "a", "name": "web_fetch", "args": {"url": "https://example.cn/a"}}]},
                {"type": "tool", "tool_call_id": "a", "name": "web_fetch", "content": "数据服务"},
            ],
        }

    def test_same_renderer_replay_and_invented_paragraph_rejected(self):
        run = self.run_fixture()
        run["output"], stats = replay_structured_report(run, "interview_prep")
        self.assertEqual(stats["accepted"], 1)
        case = {"expected": {"profile": "prep_report"}}
        self.assertEqual(check_structured_evidence(run, case)[0].status, "pass")
        run["output"] += "\n编造的新结论"
        self.assertEqual(check_structured_evidence(run, case)[0].status, "fail")

    def test_missing_tool_content_or_structured_payload_cannot_certify(self):
        run = self.run_fixture()
        run["output"], _ = replay_structured_report(run, "interview_prep")
        run["tool_messages"] = []
        case = {"expected": {"profile": "prep_report"}}
        self.assertEqual(check_structured_evidence(run, case)[0].status, "fail")
        run.pop("structured_evidence")
        self.assertEqual(check_structured_evidence(run, case)[0].status, "fail")

    def test_child_provider_ids_are_scoped_by_agent(self):
        run = self.run_fixture()
        run["tool_messages"][0]["agent_id"] = "a"
        run["tool_messages"][1]["agent_id"] = "b"
        _, stats = replay_structured_report(run, "interview_prep")
        self.assertEqual(stats["accepted"], 0)

    def test_score_and_resume_evidence_replay(self):
        run = self.run_fixture()
        data = run["structured_evidence"]
        data.update(base_records=[{"record_id": "rec1", "公司": "合成", "岗位": "开发"}], uploaded_resumes={"resume.md": "Python"})
        data["model_output"] = block("jobscout_match", {"candidates": [{"record_id": "rec1", "total": 100, "score_items": [
            {"dimension": "skills", "points": 30, "resume_file": "resume.md", "resume_quote": "Java"}
        ]}]})
        run["tool_messages"] = [
            {"type": "ai", "tool_calls": [{"id": "read", "name": "read_file", "args": {"path": "/mnt/user-data/uploads/resume.md"}}]},
            {"type": "tool", "name": "read_file", "tool_call_id": "read", "content": "Python"},
        ]
        report, _ = replay_structured_report(run, "base_match")
        self.assertIn("没有通过双侧证据校验的推荐岗位。", report)
        self.assertNotIn("| 0/100 |", report)
        self.assertNotIn("Java", report)

    def test_explicit_empty_recommendation_is_valid_but_silent_empty_or_zero_is_not(self):
        run = {"structured_evidence": {"server_date": "2026-10-22", "model_output": block("jobscout_match", {"candidates": []})}}
        run["output"], _ = replay_structured_report(run, "base_match")
        run["tool_calls"] = [{"name": "read_file", "status": "success"}]
        self.assertTrue(all(gate.status == "pass" for gate in check_base_report(run, {})))
        empty = run["output"]
        run["output"] = empty.replace("没有通过双侧证据校验的推荐岗位。", "")
        self.assertTrue(any(gate.status == "fail" for gate in check_base_report(run, {})))
        run["output"] = empty.replace("没有通过双侧证据校验的推荐岗位。", "| 排名 | 依据分 | 公司 / 岗位 | 依据 | 差距 | 动作 | ID |\n|---|---|---|---|---|---|---|\n| 1 | 0/100 | 合成 | 无 | 无 | 复核 | rec1 |")
        self.assertTrue(any(gate.gate_id == "base_scores_in_range" and gate.status == "fail" for gate in check_base_report(run, {})))

    def test_grouped_recommendations_validate_their_own_detail_score(self):
        run = {"structured_evidence": {"server_date": "2026-10-22",
            "base_records": [{"record_id": "rec1", "公司": "示例", "岗位": "开发", "要求": "Python"}],
            "uploaded_resumes": {"resume.md": "开发 Python"},
            "model_output": block("jobscout_match", {"candidates": [{"record_id": "rec1", "score_items": [
                {"dimension": "skills", "points": 30, "requirement_field": "要求", "requirement_quote": "Python", "matched_term": "Python", "resume_file": "resume.md", "resume_quote": "开发 Python"}]}]})},
            "tool_calls": [{"name": "read_file", "status": "success"}],
            "tool_messages": [{"type": "ai", "tool_calls": [{"id": "read", "name": "read_file", "args": {"path": "/mnt/user-data/uploads/resume.md"}}]},
                {"type": "tool", "name": "read_file", "tool_call_id": "read", "content": "开发 Python"}]}
        run["output"], _ = replay_structured_report(run, "base_match")
        self.assertTrue(all(gate.status == "pass" for gate in check_base_report(run, {})))
        original = run["output"]
        for broken in (original.replace("30/100", "0/100"), original.replace("### rec1", "### other"), original.replace("30/100", "101/100")):
            run["output"] = broken
            self.assertTrue(any(gate.gate_id == "base_scores_in_range" and gate.status == "fail" for gate in check_base_report(run, {})))


if __name__ == "__main__":
    unittest.main()
