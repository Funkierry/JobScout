import unittest

from evaluate_triggers import evaluate


class TriggerMetricsTests(unittest.TestCase):
    def test_unresolved_and_false_rejection_are_reported_not_hidden(self):
        report = evaluate(
            [
                {"query": "帮我做面试准备包", "should_trigger": True},
                {"query": "解释一下 RAG", "should_trigger": False},
                {"query": "处理一下那个", "should_trigger": True},
            ]
        )
        self.assertEqual(report["off_topic_leak_count"], 0)
        self.assertEqual(report["false_rejection_rate"], 0.5)
        self.assertEqual(report["unresolved_count"], 1)
        self.assertEqual(report["misclassified"][0]["index"], 3)
        self.assertEqual(report["unresolved"][0]["query"], "处理一下那个")

    def test_metrics_reveal_a_leaked_label(self):
        report = evaluate([{"query": "帮我做面试准备包", "should_trigger": False}])
        self.assertEqual(report["off_topic_leak_rate"], 1)
        self.assertIsNone(report["false_rejection_rate"])
