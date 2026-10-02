"""Fixed offline eval scenarios are readable and executable."""

import unittest
from unittest.mock import patch

from loom_npc.evals import run_evals
from loom_npc.models import MockLLM, ModelDecision


class EvalTests(unittest.TestCase):
    def test_all_bundled_scenarios_pass(self):
        result = run_evals()
        self.assertEqual(result["total"], 8)
        self.assertEqual(result["passed"], result["total"])
        self.assertTrue(all(item["passed"] for item in result["results"]))

    def test_eval_reports_behavioral_failure(self):
        wrong = ModelDecision('{"type":"speak","actor_id":"mara","target_id":"player","topic":"town"}')
        with patch.object(MockLLM, "generate_decision", return_value=wrong):
            result = run_evals()
        self.assertLess(result["passed"], result["total"])
        self.assertIn("expected", next(item for item in result["results"] if not item["passed"])["detail"])


if __name__ == "__main__":
    unittest.main()
