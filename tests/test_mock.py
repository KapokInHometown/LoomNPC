"""World-independent offline decisions through Runtime, HTTP and replay."""

import copy
import http.client
import json
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from loom_npc import Runtime, load_world
from loom_npc.core import WorldState, build_context
from loom_npc.integrations.server import create_server
from loom_npc.models import MockLLM
from loom_npc.replay import export_jsonl, replay_jsonl


WORKSHOP = Path(__file__).resolve().parents[1] / "examples" / "workshop.json"
COMMANDS = [
    ("apprentice", "进入锻造间", "BRIEFING_REQUIRED"),
    ("smith", "请向学徒说明开工计划", "SECRET_LOCKED"),
    ("apprentice", "把矿石交给工匠", "OK"),
    ("smith", "请向学徒说明开工计划", "OK"),
    ("apprentice", "进入锻造间", "OK"),
]


class MockDecisionTests(unittest.TestCase):
    def assert_completed(self, world):
        self.assertEqual(world["quests"], {"materials_ready": True, "briefed": True, "started": True})
        self.assertEqual(world["actors"]["apprentice"]["location"], "forge")
        self.assertEqual(world["actors"]["apprentice"]["inventory"], [])
        self.assertEqual(world["actors"]["smith"]["inventory"], ["ore"])
        self.assertEqual(world["actors"]["smith"]["trust"]["apprentice"], 2)
        self.assertIn("forge_plan", world["actors"]["apprentice"]["belief"]["known_facts"])
        self.assertEqual(world["tick"], 3)

    def assert_trace(self, trace, code):
        self.assertEqual(trace["source"], "adapter")
        self.assertEqual(trace["verification"]["code"], code, trace["errors"])
        self.assertEqual(trace["status"], "executed" if code == "OK" else "rejected")
        if code != "OK":
            self.assertEqual(trace["before"], trace["after"])
            self.assertEqual(trace["state_diff"], [])

    def test_workshop_commands_complete_runtime_offline_and_replay(self):
        runs = []
        with patch.dict(os.environ, {}, clear=True), \
                patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("External HTTP")):
            for _ in range(2):
                runtime = Runtime(load_world(WORKSHOP))
                for actor, command, code in COMMANDS:
                    self.assert_trace(runtime.step(actor, command), code)
                self.assert_completed(runtime.world.to_dict())
                runs.append(runtime)
        self.assertEqual(runs[0].traces, runs[1].traces)
        with patch.object(MockLLM, "generate_decision", side_effect=AssertionError("Replay called adapter")):
            replay = replay_jsonl(export_jsonl(runs[0].traces))
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["world"], runs[0].world.to_dict())
        self.assertEqual(replay["model_calls"], 0)

    def test_all_world_identifiers_can_be_renamed(self):
        data = load_world(WORKSHOP).to_dict()
        names = [data["id"]] + [key for field in ("actors", "locations", "items", "facts", "quests") for key in data[field]]
        renamed = {name: "entity_" + str(index) for index, name in enumerate(names)}
        encoded = json.dumps(data, ensure_ascii=False)
        for old, new in renamed.items():
            encoded = encoded.replace(json.dumps(old), json.dumps(new))
        runtime = Runtime(WorldState(json.loads(encoded)))
        for actor, command, code in COMMANDS:
            self.assert_trace(runtime.step(renamed[actor], command), code)
        world = runtime.world.to_dict()
        self.assertTrue(all(world["quests"].values()))
        self.assertEqual(world["actors"][renamed["apprentice"]]["location"], renamed["forge"])
        self.assertEqual(world["actors"][renamed["smith"]]["inventory"], [renamed["ore"]])
        self.assertTrue(replay_jsonl(export_jsonl(runtime.traces))["ok"])

    def test_lantern_chinese_and_english_routes_still_complete(self):
        for commands in [
            [("player", "递信"), ("mara", "灯塔的秘密"), ("mara", "钥匙"), ("player", "进入灯塔")],
            [("player", "DELIVER LETTER"), ("mara", "SECRET"), ("mara", "GIVE KEY"), ("player", "GO TO TOWER")],
        ]:
            runtime = Runtime()
            for actor, text in commands:
                self.assertEqual(runtime.step(actor, text)["status"], "executed")
            self.assertEqual(runtime.world.to_dict()["actors"]["player"]["location"], "tower")
        for text, topic in [("你好", "greeting"), ("介绍小镇", "town"), ("你还记得那封失落的信吗", "letter_request")]:
            self.assertEqual(Runtime().step("mara", text)["action"]["topic"], topic)
        for text, place in [("前往旅店", "inn"), ("move inn", "inn")]:
            self.assertEqual(Runtime().step("player", text)["action"]["location_id"], place)

    def test_unknown_unowned_unreachable_or_forbidden_requests_do_not_guess(self):
        cases = [("apprentice", "把木材交给工匠"), ("apprentice", "把矿石交给陌生人"),
                 ("apprentice", "进入城堡"), ("apprentice", "进入前院"),
                 ("apprentice", "说明开工计划"), ("smith", "交矿石"),
                 ("apprentice", "谈谈天气"), ("smith", "keyword"),
                 ("apprentice", "give ore to ghost"), ("apprentice", "把矿石交给陌生工匠")]
        for actor, text in cases:
            runtime = Runtime(load_world(WORKSHOP))
            trace = runtime.step(actor, text)
            self.assertEqual(trace["status"], "model_error", text)
            self.assertIsNone(trace["action"])
            self.assertEqual(trace["before"], trace["after"])
            self.assertEqual(trace["state_diff"], [])
            self.assertTrue(replay_jsonl(export_jsonl(runtime.traces))["ok"])
        data = load_world(WORKSHOP).to_dict()
        data["actors"]["apprentice"]["allowed_actions"] = ["speak"]
        self.assertEqual(Runtime(WorldState(data)).step("apprentice", "交矿石")["status"], "model_error")
        self.assertEqual(Runtime().step("ivo", "你好")["status"], "model_error")

    def test_multiple_visible_targets_require_explicit_name(self):
        data = load_world(WORKSHOP).to_dict()
        data["actors"]["visitor"] = dict(copy.deepcopy(data["actors"]["smith"]), id="visitor", name="访客", trust={})
        for ordered in [data, json.loads(json.dumps(data, sort_keys=True))]:
            runtime = Runtime(WorldState(ordered))
            trace = runtime.step("apprentice", "交矿石")
            self.assertEqual(trace["status"], "model_error")
            trace = runtime.step("apprentice", "把矿石交给工匠")
            self.assertEqual(trace["status"], "executed")
            self.assertEqual(trace["action"]["target_id"], "smith")

    def test_alias_collisions_and_multiple_items_are_explicit(self):
        data = load_world(WORKSHOP).to_dict()
        data["items"]["wood"] = {"id": "wood", "name": "木材", "description": "另一份原料", "aliases": ["原料"]}
        data["items"]["ore"]["aliases"] = ["原料"]
        data["actors"]["apprentice"]["inventory"].append("wood")
        runtime = Runtime(WorldState(data))
        for text in ("交原料", "把矿石和木材交给工匠"):
            self.assertEqual(runtime.step("apprentice", text)["status"], "model_error")
        self.assertEqual(runtime.step("apprentice", "交矿石")["action"]["item_id"], "ore")

    def test_context_labels_do_not_reveal_hidden_entities_or_rules(self):
        data = load_world().to_dict()
        data["items"]["key"]["aliases"] = ["HIDDEN_ITEM_ALIAS"]
        data["facts"]["lighthouse_secret"]["aliases"] = ["HIDDEN_FACT_ALIAS"]
        data["locations"]["tower"]["aliases"] = ["HIDDEN_LOCATION_ALIAS"]
        context = build_context(WorldState(data), "ivo", "你好")
        encoded = json.dumps(context)
        self.assertNotIn("HIDDEN_", encoded)
        self.assertNotIn("rules", context)
        self.assertNotIn("quests", context)

    def test_alias_schema_is_optional_but_validated(self):
        base = load_world(WORKSHOP).to_dict()
        for field, key in [("items", "ore"), ("locations", "forge"), ("facts", "forge_plan")]:
            for aliases in ["alias", [1], [""], ["  "], ["duplicate", "duplicate"]]:
                data = copy.deepcopy(base)
                data[field][key]["aliases"] = aliases
                with self.subTest(field=field, aliases=aliases), self.assertRaises(ValueError):
                    WorldState(data)
        for records in base.values():
            if isinstance(records, dict):
                for record in records.values():
                    if isinstance(record, dict):
                        record.pop("aliases", None)
        runtime = Runtime(WorldState(base))
        self.assertEqual(runtime.step("apprentice", "give ore")["status"], "executed")
        self.assertEqual(runtime.step("smith", "forge_plan")["status"], "executed")
        self.assertEqual(runtime.step("apprentice", "进入锻造间")["status"], "executed")
        self.assertTrue(replay_jsonl(export_jsonl(runtime.traces))["ok"])

    def test_workshop_task_through_real_http_api_and_reset(self):
        server = create_server(load_world(WORKSHOP), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def api(path, data=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                body = None if data is None else json.dumps(data).encode("utf-8")
                connection.request("GET" if data is None else "POST", path, body=body,
                                   headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                content = response.read()
                self.assertEqual(response.status, 200, content)
                return content if path == "/api/trace" else json.loads(content)
            finally:
                connection.close()

        try:
            initial = api("/api/state")["world"]
            for actor, text, code in COMMANDS:
                state = api("/api/step", {"actor_id": actor, "input": text})
                self.assert_trace(state["trace"], code)
            self.assert_completed(state["world"])
            replay = api("/api/replay", {"jsonl": api("/api/trace").decode("utf-8")})
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(replay["world"], state["world"])
            self.assertEqual(replay["model_calls"], 0)
            self.assertEqual(api("/api/reset", {})["world"], initial)
            for actor, text, code in COMMANDS[2:]:
                self.assert_trace(api("/api/step", {"actor_id": actor, "input": text})["trace"], code)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
