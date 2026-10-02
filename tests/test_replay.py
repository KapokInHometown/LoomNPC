"""Replay must recompute outcomes, not just echo stored snapshots."""

import copy
import json
import unittest
from unittest.mock import patch

from loom_npc import Runtime
from loom_npc.models import MockLLM
from loom_npc.replay import export_jsonl, replay_jsonl


class ReplayTests(unittest.TestCase):
    def quest(self):
        runtime = Runtime()
        for actor, text in [("mara", "秘密"), ("player", "交信"), ("mara", "秘密"), ("mara", "钥匙"), ("player", "进入灯塔")]:
            runtime.step(actor, text)
        return runtime

    def test_complete_quest_roundtrip_never_calls_model(self):
        runtime = self.quest()
        with patch.object(MockLLM, "generate_decision", side_effect=AssertionError("Replay called model")):
            result = replay_jsonl(export_jsonl(runtime.traces))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 5)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["world"], runtime.world.to_dict())

    def test_context_and_witnesses_survive_reordered_json_keys(self):
        runtime = Runtime()
        runtime.step("ivo", "前往广场")
        runtime.step("mara", "你好")
        runtime.step("player", "前往旅店")
        encoded = export_jsonl(runtime.traces)
        decoded = [json.loads(line) for line in encoded.splitlines()]
        self.assertNotEqual(list(runtime.traces[0]["before"]["actors"]), list(decoded[0]["before"]["actors"]))
        result = replay_jsonl(encoded)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["world"], runtime.world.to_dict())

    def test_tampered_world_context_action_result_and_diff_are_rejected(self):
        runtime = self.quest()
        mutations = [
            lambda rows: rows[1]["after"]["quests"].update(letter_delivered=False),
            lambda rows: rows[1]["context"]["actor"].update(inventory=["key"]),
            lambda rows: rows[2]["action"].update(topic="town"),
            lambda rows: rows[0]["verification"].update(ok=True),
            lambda rows: rows[1]["execution"].update(message="forged execution"),
            lambda rows: rows[1].update(state_diff=[]),
            lambda rows: rows[1]["before"]["actors"]["mara"]["trust"].update(player=99),
            lambda rows: rows[1].update(tick=99),
        ]
        for mutate in mutations:
            traces = copy.deepcopy(runtime.traces)
            mutate(traces)
            result = replay_jsonl(export_jsonl(traces))
            self.assertFalse(result["ok"], result)
            self.assertTrue(result["error"])

    def test_fixed_parse_rejection_trace_replays(self):
        runtime = Runtime()
        runtime.step("mara", "bad", {"type": "speak", "text": "arbitrary"})
        runtime.step("ghost", "你好")
        result = replay_jsonl(export_jsonl(runtime.traces))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["world"]["tick"], 0)

    def test_model_failure_boundary_is_explicit(self):
        class FailingModel:
            def generate_decision(self, context):
                raise RuntimeError("offline")

        runtime = Runtime(adapter=FailingModel())
        runtime.step("mara", "你好")
        result = replay_jsonl(export_jsonl(runtime.traces))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["recorded_failures"], 1)
        self.assertTrue(result["limitation"])

    def test_empty_invalid_and_truncated_jsonl_fail_explicitly(self):
        for text in ("", "not json", "[]", "{}", '{"id":'):
            with self.subTest(text=text):
                result = replay_jsonl(text)
                self.assertFalse(result["ok"])
                self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
