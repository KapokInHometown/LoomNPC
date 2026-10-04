"""Scenario configuration must preserve shared boundaries and atomic replay."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from loom_npc import Runtime, load_world
from loom_npc.core import WorldState, build_context, parse_action
from loom_npc.core.executor import Executor
from loom_npc.models import MockLLM
from loom_npc.replay import export_jsonl, replay_jsonl


WORKSHOP = Path(__file__).resolve().parents[1] / "examples" / "workshop.json"


def step(runtime, action):
    return runtime.step(action["actor_id"], "固定规则输入", proposed_action=action)


class ScenarioRuleTests(unittest.TestCase):
    def test_second_world_completes_all_three_actions_and_replays_offline(self):
        runtime = Runtime(load_world(WORKSHOP))
        self.assertIsInstance(runtime.adapter, MockLLM)
        move = {"type": "move", "actor_id": "apprentice", "location_id": "forge"}
        speak = {"type": "speak", "actor_id": "smith", "target_id": "apprentice", "topic": "forge_plan"}
        give = {"type": "give", "actor_id": "apprentice", "target_id": "smith", "item_id": "ore"}
        before = runtime.world.to_dict()
        with patch.object(MockLLM, "generate_decision", side_effect=AssertionError("Unexpected model call")):
            for action, code in ((move, "BRIEFING_REQUIRED"), (speak, "SECRET_LOCKED")):
                trace = step(runtime, action)
                self.assertEqual(trace["verification"]["code"], code)
                self.assertEqual(trace["after"], before)
                self.assertEqual(trace["state_diff"], [])
            delivery = step(runtime, give)
            self.assertEqual(delivery["status"], "executed")
            self.assertTrue(delivery["after"]["quests"]["materials_ready"])
            self.assertEqual(delivery["after"]["actors"]["smith"]["trust"]["apprentice"], 2)
            self.assertEqual(step(runtime, move)["verification"]["code"], "BRIEFING_REQUIRED")
            briefing = step(runtime, speak)
            self.assertEqual(briefing["status"], "executed")
            self.assertTrue(briefing["after"]["quests"]["briefed"])
            self.assertIn("forge_plan", briefing["after"]["actors"]["apprentice"]["belief"]["known_facts"])
            movement = step(runtime, move)
            self.assertEqual(movement["status"], "executed")
            self.assertTrue(movement["after"]["quests"]["started"])
            replay = replay_jsonl(export_jsonl(runtime.traces))
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["model_calls"], 0)
        self.assertEqual(replay["world"], runtime.world.to_dict())
        self.assertEqual(runtime.world.tick, 3)
        for actor in runtime.world.actors.values():
            self.assertEqual(len(actor.memories), 3)
            self.assertEqual(actor.memories[-1].event_id, movement["execution"]["event"]["id"])
        self.assertNotIn("rules", briefing["context"])
        self.assertNotIn("forge_plan", json.dumps(build_context(load_world(WORKSHOP), "apprentice", "计划")))

    def test_no_rules_means_only_shared_checks_and_no_quest_effects(self):
        data = load_world().to_dict()
        data.pop("rules")
        runtime = Runtime(WorldState(data))
        delivery = step(runtime, {"type": "give", "actor_id": "player", "target_id": "mara", "item_id": "letter"})
        self.assertEqual(delivery["status"], "executed")
        self.assertFalse(delivery["after"]["quests"]["letter_delivered"])
        self.assertEqual(delivery["after"]["actors"]["mara"]["trust"]["player"], 0)
        self.assertEqual(step(runtime, {"type": "move", "actor_id": "player", "location_id": "tower"})["status"], "executed")
        self.assertTrue(replay_jsonl(export_jsonl(runtime.traces))["ok"])

    def test_letter_reward_is_once_and_key_requires_recipient_quest_and_trust(self):
        runtime = Runtime()
        runtime.step("player", "交信")
        returned = step(runtime, {"type": "give", "actor_id": "mara", "target_id": "player", "item_id": "letter"})
        self.assertEqual(returned["status"], "executed")
        delivery = runtime.step("player", "交信")
        self.assertEqual(delivery["after"]["actors"]["mara"]["trust"]["player"], 1)
        self.assertTrue(delivery["execution"]["message"].endswith("失落的信送达，守灯人对旅人的信任增加。"))
        action = {"type": "give", "actor_id": "mara", "target_id": "player", "item_id": "key"}
        base = runtime.world.to_dict()
        for quest, trust, target in ((False, 1, "player"), (True, 0, "player"), (True, 1, "ivo")):
            data = copy.deepcopy(base)
            data["quests"]["letter_delivered"] = quest
            data["actors"]["mara"]["trust"]["player"] = trust
            data["actors"]["ivo"]["location"] = "square"
            other = Runtime(WorldState(data))
            trace = step(other, {**action, "target_id": target})
            self.assertEqual(trace["verification"]["code"], "QUEST_LOCKED")
            self.assertEqual(trace["before"], trace["after"])

    def test_shared_checks_run_before_scene_rules_and_executor_rechecks(self):
        data = load_world(WORKSHOP).to_dict()
        data["actors"]["smith"]["allowed_actions"] = ["speak"]
        runtime = Runtime(WorldState(data))
        forbidden = step(runtime, {"type": "move", "actor_id": "smith", "location_id": "forge"})
        self.assertEqual(forbidden["verification"]["code"], "ACTION_FORBIDDEN")
        unknown = step(runtime, {"type": "speak", "actor_id": "apprentice", "target_id": "smith", "topic": "forge_plan"})
        self.assertEqual(unknown["verification"]["code"], "KNOWLEDGE_BOUNDARY")
        action = parse_action({"type": "move", "actor_id": "apprentice", "location_id": "forge"})
        before = runtime.world.to_dict()
        with self.assertRaisesRegex(ValueError, "BRIEFING_REQUIRED"):
            Executor().execute(runtime.world, action, "apprentice")
        self.assertEqual(runtime.world.to_dict(), before)

    def test_effect_failure_rolls_back_transfer_quests_trust_events_and_memory(self):
        runtime = Runtime(load_world(WORKSHOP))
        action = {"type": "give", "actor_id": "apprentice", "target_id": "smith", "item_id": "ore"}

        def fail_after_effects(before, state, proposed):
            from loom_npc.core.rules import apply_rule_effects

            apply_rule_effects(before, state, proposed)
            self.assertTrue(state["quests"]["materials_ready"])
            self.assertEqual(state["actors"]["smith"]["inventory"], ["ore"])
            raise RuntimeError("effect failure")

        before = runtime.world.to_dict()
        with patch("loom_npc.core.executor.apply_rule_effects", side_effect=fail_after_effects):
            trace = step(runtime, action)
        self.assertEqual(trace["status"], "execution_error")
        self.assertEqual(trace["before"], trace["after"])
        self.assertEqual(trace["state_diff"], [])
        self.assertEqual(runtime.world.to_dict(), before)

    def test_rule_tampering_breaks_recomputed_replay(self):
        runtime = Runtime(load_world(WORKSHOP))
        step(runtime, {"type": "give", "actor_id": "apprentice", "target_id": "smith", "item_id": "ore"})
        traces = copy.deepcopy(runtime.traces)
        for snapshot in ("before", "after"):
            traces[0][snapshot]["rules"][0]["effects"][1]["amount"] = 9
        replay = replay_jsonl(export_jsonl(traces))
        self.assertFalse(replay["ok"])
        self.assertIn("Recomputed world", replay["error"])

    def test_validation_rejects_unknown_fields_types_and_references(self):
        base = load_world(WORKSHOP).to_dict()
        mutations = [
            lambda d: d.update(rules={}),
            lambda d: d["rules"].append(copy.deepcopy(d["rules"][0])),
            lambda d: d["rules"][0].update(id=[]),
            lambda d: d["rules"][0].update(code="extra"),
            lambda d: d["rules"][0]["match"].update(type="teleport"),
            lambda d: d["rules"][0]["match"].update(location_id="forge"),
            lambda d: d["rules"][0]["match"].update(actor_id="ghost"),
            lambda d: d["rules"][0]["match"].update(item_id="missing"),
            lambda d: d["rules"][1]["match"].update(topic="missing"),
            lambda d: d["rules"][2]["match"].update(location_id="missing"),
            lambda d: d["rules"][0].update(requires=[{"type": "script", "code": "x"}]),
            lambda d: d["rules"][0].update(requires={}),
            lambda d: d["rules"][2]["requires"][0].update(value=1),
            lambda d: d["rules"][2]["requires"][0].update(quest_id="missing"),
            lambda d: d["rules"][2].update(requires=[{"type": "target_is", "actor_id": "smith"}]),
            lambda d: d["rules"][0].update(requires=[{"type": "trust_at_least", "actor_id": "smith", "target_id": "apprentice", "minimum": True}]),
            lambda d: d["rules"][0].update(requires=[{"type": "has_item", "actor_id": "smith", "item_id": "missing"}]),
            lambda d: d["rules"][0]["reject"].update(code=""),
            lambda d: d["rules"][0].update(summary_suffix=[]),
            lambda d: d["rules"][0].update(effects={}),
            lambda d: d["rules"][0]["effects"][0].update(type="set_path"),
            lambda d: d["rules"][0]["effects"][0].update(value=1),
            lambda d: d["rules"][0]["effects"][0].update(quest_id="missing"),
            lambda d: d["rules"][0]["effects"][1].update(amount=-1),
            lambda d: d["rules"][0]["effects"][1].update(amount=True),
            lambda d: d["rules"][0]["effects"][1].update(actor_id="ghost"),
            lambda d: d["rules"][0]["effects"][1].update(target_id="ghost"),
            lambda d: d["rules"][0]["effects"][1].update(when=[{}]),
        ]
        for index, mutate in enumerate(mutations):
            data = copy.deepcopy(base)
            mutate(data)
            with self.subTest(index=index), self.assertRaises(ValueError):
                WorldState(data)


if __name__ == "__main__":
    unittest.main()
