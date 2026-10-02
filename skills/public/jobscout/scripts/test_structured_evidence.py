import json
import unittest
from pathlib import Path

from grade_runs import check_followup_gap, check_structured_evidence, replay_structured_report
from evaluate_guardrail_fixtures import evaluate

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
        self.assertIn("| 0/100 |", report)
        self.assertNotIn("Java", report)


if __name__ == "__main__":
    unittest.main()
