from __future__ import annotations

import unittest

from grade_runs import build_report, evaluate_run


def review(*gate_names: str, score: float = 4.0) -> dict:
    return {
        "hard_gates": {name: True for name in gate_names},
        "scores": {
            "intent_flow": score,
            "output_contract": score,
            "evidence_facts": score,
            "job_analysis": score,
            "resume_base_grounding": score,
            "reliability_cost": score,
        },
    }


def runtime() -> dict:
    return {
        "model": "fixture-model",
        "skill_digest": "sha256:fixture",
        "fixture_version": "grader-test-v1",
        "started_at": "2026-09-21T00:00:00Z",
    }


def question_table(count: int, prefix: str) -> str:
    rows = [
        "| # | 题目 | 标签 | 考察点与准备方向 | 来源 |",
        "|---|---|---|---|---|",
    ]
    for index in range(1, count + 1):
        rows.append(f"| {index} | {prefix}{index} | 通用 | 要点 | [来源](https://example.cn/{prefix}/{index}) |")
    return "\n".join(rows)


def prep_output(extra_url: str = "") -> str:
    return f"""# 字节跳动 · 后端开发 面试准备包

## 公司速览
### 业务与产品
- 业务事实 [来源](https://example.cn/company)

## 岗位拆解
| 类别 | 内容 | 证据强度 | 来源 |
|---|---|---|---|
| 必备技能 | Java | 真实JD | [来源](https://example.cn/jd) |

## 面试题预测
### 技术 / 岗位题
{question_table(8, "tech")}

### 行为题
{question_table(4, "behavior")}

## 证据边界与后续建议
- 已核验公开资料。{extra_url}
"""


class GradeRunsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.prep_case = {
            "id": "flow-001-minimal-complete",
            "mode": "interview_prep",
            "category": "flow",
            "risk": "critical",
            "tier": "smoke",
            "tags": ["minimal_input", "three_tasks"],
            "expected": {"profile": "prep_report", "action": "research_and_report"},
        }

    def prep_run(self) -> dict:
        return {
            "case_id": self.prep_case["id"],
            "run_id": "run-prep-pass",
            "output": prep_output(),
            "tool_calls": [
                {"name": "task", "status": "success", "started_at_ms": 0, "ended_at_ms": 10},
                {"name": "task", "status": "success", "started_at_ms": 1, "ended_at_ms": 11},
                {"name": "task", "status": "success", "started_at_ms": 2, "ended_at_ms": 12},
            ],
            "runtime": runtime(),
            "environment": {"valid_source_count": 3, "live_web": False},
            "metrics": {"input_tokens": 1000, "output_tokens": 500, "latency_ms": 1200},
            "human_review": review("no_fabrication", "sources_support_claims", "analysis_grounded"),
        }

    def test_prep_report_passes_observable_contract(self) -> None:
        result = evaluate_run(self.prep_run(), self.prep_case, 80)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["quality_score"], 80)
        self.assertTrue(all(gate["status"] == "pass" for gate in result["hard_gates"]))

    def test_banned_source_is_hard_failure(self) -> None:
        run = self.prep_run()
        run["run_id"] = "run-prep-banned"
        run["output"] = prep_output("https://www.linkedin.com/jobs/1")
        result = evaluate_run(run, self.prep_case, 80)
        self.assertEqual(result["status"], "hard_fail")
        failed_ids = {gate["gate_id"] for gate in result["hard_gates"] if gate["status"] == "fail"}
        self.assertIn("no_banned_source_domains", failed_ids)

    def test_missing_human_review_does_not_auto_pass(self) -> None:
        run = self.prep_run()
        run["run_id"] = "run-prep-review"
        run.pop("human_review")
        result = evaluate_run(run, self.prep_case, 80)
        self.assertEqual(result["status"], "needs_human_review")
        self.assertIsNone(result["quality_score"])

    def test_base_report_checks_exact_sections_and_top_ten(self) -> None:
        case = {
            "id": "base-001-complete",
            "mode": "feishu_base_matching",
            "category": "base",
            "risk": "critical",
            "tier": "smoke",
            "tags": ["base", "no_web"],
            "expected": {"profile": "base_report", "action": "read_and_match"},
        }
        rows = "\n".join(
            f"| {index} | {90 - index} | 公司 / 岗位{index} | 匹配 | 差距 | 建议 | rec-{index} |"
            for index in range(1, 11)
        )
        output = f"""# 简历 × 飞书岗位匹配报告

## 候选人画像
- 方向：AI 产品

## 推荐岗位
| 排名 | 匹配度 | 公司 / 岗位 | 核心匹配 | 主要差距 | 建议动作 | 记录标识 |
|---|---:|---|---|---|---|---|
{rows}

## 匹配依据
- 评分口径完整。

## 风险与数据边界
- 共读取十条记录。
"""
        run = {
            "case_id": case["id"],
            "run_id": "run-base-pass",
            "output": output,
            "tool_calls": [{"name": "read_file", "status": "success"}],
            "runtime": runtime(),
            "human_review": review("no_fabrication", "base_grounding", score=5),
        }
        result = evaluate_run(run, case, 80)
        self.assertEqual(result["status"], "passed")

        run["run_id"] = "run-base-no-resume-read"
        run["tool_calls"] = []
        result = evaluate_run(run, case, 80)
        self.assertEqual(result["status"], "hard_fail")

        run["run_id"] = "run-base-too-many"
        run["tool_calls"] = [{"name": "read_file", "status": "success"}]
        run["output"] = output.replace(rows, rows + "\n| 11 | 70 | 公司 / 岗位11 | 匹配 | 差距 | 建议 | rec-11 |")
        result = evaluate_run(run, case, 80)
        self.assertEqual(result["status"], "hard_fail")

    def test_report_summarizes_status_and_metrics(self) -> None:
        passed = evaluate_run(self.prep_run(), self.prep_case, 80)
        failed_run = self.prep_run()
        failed_run["run_id"] = "run-prep-short"
        failed_run["tool_calls"] = failed_run["tool_calls"][:2]
        failed = evaluate_run(failed_run, self.prep_case, 80)
        report = build_report([passed, failed], {"suite": "fixture", "schema_version": "1.0"}, 80)
        self.assertEqual(report["summary"]["runs"], 2)
        self.assertEqual(report["summary"]["passed"], 1)
        self.assertEqual(report["summary"]["hard_failed"], 1)
        self.assertEqual(report["summary"]["total_input_tokens"], 2000)
        self.assertEqual(report["slices"]["tag"]["three_tasks"]["runs"], 2)


if __name__ == "__main__":
    unittest.main()
