"""Behavioral regression tests for the complete offline decision pipeline."""

import copy
import json
import unittest
from unittest.mock import patch

from loom_npc import Runtime, load_world
from loom_npc.core import ActionParseError, WorldState, build_context, parse_action
from loom_npc.core.types import BeliefState, Memory, NPC, Observation, Trace
from loom_npc.memory import retrieve_memories
from loom_npc.models import ModelDecision


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()

    def test_domain_snapshots_are_detached(self):
        world = load_world()
        self.assertIsInstance(world.actors["mara"], NPC)
        self.assertIsInstance(world.actors["mara"].belief, BeliefState)
        snapshot = world.to_dict()
        snapshot["actors"]["mara"]["inventory"].clear()
        world.actors["mara"].inventory.clear()
        self.assertEqual(world.to_dict()["actors"]["mara"]["inventory"], ["key"])
        trace = Trace({"nested": {"value": 1}})
        data = trace.to_dict()
        data["nested"]["value"] = 2
        self.assertEqual(trace.to_dict()["nested"]["value"], 1)

    def test_parser_accepts_only_exact_schema(self):
        action = {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "greeting"}
        self.assertEqual(parse_action(json.dumps(action)).to_dict(), action)
        bad = ["not json", "[]", None, [], {**action, "text": "arbitrary prose"},
               {**action, "topic": ""}, {**action, "topic": 12},
               {**action, "type": "teleport"}, {**action, "type": []},
               {"type": "move", "actor_id": "mara"},
               '{"type":"move","actor_id":"mara","actor_id":"player","location_id":"inn"}']
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ActionParseError):
                parse_action(value)

    def test_context_excludes_unknown_facts_and_remote_actors(self):
        context = build_context(self.runtime.world, "mara", "你好")
        self.assertNotIn("北侧暗礁", json.dumps(context, ensure_ascii=False))
        self.assertEqual([actor["id"] for actor in context["observation"]["visible_actors"]], ["player"])
        self.assertNotIn("actors", context)
        self.assertNotIn("quests", context)
        self.assertEqual(context["belief"]["known_facts"], [fact["id"] for fact in context["known_facts"]])
        self.assertIsInstance(Observation(**context["observation"]), Observation)

    def test_secret_and_knowledge_rejections_leave_entire_world_unchanged(self):
        before = self.runtime.world.to_dict()
        secret = self.runtime.step("mara", "灯塔的秘密")
        unknown = self.runtime.step("mara", "越界测试", {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "smuggler_route"})
        self.assertEqual(secret["verification"]["code"], "SECRET_LOCKED")
        self.assertEqual(unknown["verification"]["code"], "KNOWLEDGE_BOUNDARY")
        for trace in (secret, unknown):
            self.assertEqual(trace["status"], "rejected")
            self.assertEqual(trace["before"], trace["after"])
            self.assertEqual(trace["state_diff"], [])
            self.assertFalse(trace["execution"]["ok"])
        self.assertEqual(self.runtime.world.to_dict(), before)

    def test_complete_quest_and_memory_provenance(self):
        rejected = self.runtime.step("mara", "把钥匙交给我")
        self.assertEqual(rejected["verification"]["code"], "QUEST_LOCKED")
        delivery = self.runtime.step("player", "交信")
        secret = self.runtime.step("mara", "灯塔的秘密")
        key = self.runtime.step("mara", "把钥匙交给我")
        movement = self.runtime.step("player", "进入灯塔")
        for trace in (delivery, secret, key, movement):
            self.assertEqual(trace["status"], "executed")
            self.assertTrue(trace["state_diff"])
        world = self.runtime.world.to_dict()
        self.assertEqual(world["actors"]["mara"]["trust"]["player"], 1)
        self.assertEqual(world["actors"]["player"]["location"], "tower")
        self.assertEqual(world["actors"]["player"]["inventory"], ["key"])
        self.assertTrue(world["quests"]["letter_delivered"])
        self.assertTrue(world["quests"]["key_given"])
        self.assertIn("lighthouse_secret", world["actors"]["player"]["belief"]["known_facts"])
        self.assertEqual(secret["context"]["memories"][0]["event_id"], delivery["execution"]["event"]["id"])
        self.assertIsInstance(self.runtime.world.actors["mara"].memories[0], Memory)
        self.assertEqual(world["tick"], 4)

    def test_private_dialogue_does_not_leak_into_remote_memory_or_context(self):
        self.runtime.step("player", "交信")
        self.runtime.step("mara", "灯塔的秘密")
        for actor_id in ("ivo", "orin"):
            context = build_context(self.runtime.world, actor_id, "你好")
            self.assertNotIn("旧航海图", json.dumps(context, ensure_ascii=False))
            self.assertEqual(context["memories"], [])

    def test_verifier_identity_ownership_proximity_and_location_rules(self):
        cases = [
            ("mara", {"type": "move", "actor_id": "player", "location_id": "inn"}, "ACTOR_MISMATCH"),
            ("mara", {"type": "move", "actor_id": "mara", "location_id": "ocean"}, "LOCATION_NOT_FOUND"),
            ("mara", {"type": "move", "actor_id": "mara", "location_id": "square"}, "NOT_CONNECTED"),
            ("player", {"type": "move", "actor_id": "player", "location_id": "tower"}, "KEY_REQUIRED"),
            ("mara", {"type": "give", "actor_id": "mara", "target_id": "player", "item_id": "letter"}, "NOT_OWNER"),
            ("mara", {"type": "give", "actor_id": "mara", "target_id": "player", "item_id": "sword"}, "ITEM_NOT_FOUND"),
            ("ivo", {"type": "speak", "actor_id": "ivo", "target_id": "player", "topic": "greeting"}, "OUT_OF_REACH"),
            ("mara", {"type": "speak", "actor_id": "mara", "target_id": "ghost", "topic": "greeting"}, "TARGET_NOT_FOUND"),
            ("mara", {"type": "speak", "actor_id": "mara", "target_id": "mara", "topic": "greeting"}, "SELF_TARGET"),
            ("mara", {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "nonexistent"}, "TOPIC_NOT_FOUND"),
        ]
        before = self.runtime.world.to_dict()
        for actor_id, action, code in cases:
            with self.subTest(code=code):
                trace = self.runtime.step(actor_id, "test", action)
                self.assertEqual(trace["verification"]["code"], code)
                self.assertEqual(self.runtime.world.to_dict(), before)

    def test_memory_retrieval_is_actor_scoped_and_detached(self):
        self.runtime.step("player", "交信")
        self.runtime.step("mara", "你好")
        actor = self.runtime.world.to_dict()["actors"]["mara"]
        memories = retrieve_memories(actor, "失落的信", limit=1)
        self.assertEqual(memories[0]["event_id"], "event-0001")
        memories[0]["summary"] = "changed"
        self.assertNotEqual(actor["memories"][0]["summary"], "changed")

    def test_model_parse_and_execution_failures_are_explicit_and_atomic(self):
        class FailingModel:
            def generate_decision(self, context):
                raise RuntimeError("sensitive adapter internals")

        class MalformedModel:
            def generate_decision(self, context):
                return ModelDecision("not json")

        before = self.runtime.world.to_dict()
        self.runtime.adapter = FailingModel()
        model_trace = self.runtime.step("mara", "你好")
        self.assertEqual(model_trace["status"], "model_error")
        self.assertNotIn("sensitive adapter internals", json.dumps(model_trace))
        self.runtime.adapter = MalformedModel()
        self.assertEqual(self.runtime.step("mara", "你好")["status"], "parse_error")
        action = {"type": "give", "actor_id": "player", "target_id": "mara", "item_id": "letter"}
        with patch.object(self.runtime.world, "_commit", side_effect=RuntimeError("storage unavailable")):
            trace = self.runtime.step("player", "交信", action)
        self.assertEqual(trace["status"], "execution_error")
        self.assertEqual(trace["state_diff"], [])
        self.assertEqual(self.runtime.world.to_dict(), before)
        self.assertEqual(len(self.runtime.traces), 3)

    def test_adapter_cannot_mutate_context_or_world(self):
        class MutatingModel:
            def generate_decision(self, context):
                context["actor"]["inventory"].append("letter")
                context["known_facts"].clear()
                return ModelDecision('{"type":"speak","actor_id":"mara","target_id":"player","topic":"greeting"}')

        self.runtime.adapter = MutatingModel()
        trace = self.runtime.step("mara", "你好")
        self.assertEqual(trace["status"], "executed")
        self.assertEqual(trace["context"]["actor"]["inventory"], ["key"])
        self.assertEqual(self.runtime.world.to_dict()["actors"]["mara"]["inventory"], ["key"])

    def test_configuration_rejects_malformed_history_and_fields(self):
        base = load_world().to_dict()
        changes = [
            lambda data: data.update(events=[{}]),
            lambda data: data["actors"]["mara"].update(memories=[{}]),
            lambda data: data["facts"]["greeting"].update(text={}),
            lambda data: data["locations"]["square"].update(name=[]),
            lambda data: data["locations"]["square"].update(connections=[[]]),
            lambda data: data["actors"]["mara"].update(inventory=["letter"]),
            lambda data: data["actors"]["mara"]["belief"].update(known_facts=["unknown"]),
            lambda data: data.update(tick=True),
        ]
        for change in changes:
            data = copy.deepcopy(base)
            change(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                WorldState.from_dict(data)
        self.runtime.step("mara", "你好")
        changed = self.runtime.world.to_dict()
        changed["actors"]["ivo"]["memories"] = copy.deepcopy(changed["actors"]["player"]["memories"])
        with self.assertRaisesRegex(ValueError, "did not witness"):
            WorldState.from_dict(changed)


if __name__ == "__main__":
    unittest.main()
