"""Cross-world presentation contracts and persistent HTTP demo sessions."""

import copy
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from loom_npc import load_world
from loom_npc.integrations.demo import demo_presentation, trace_filename
from loom_npc.integrations.server import DemoSession, create_server
from loom_npc.models import MockLLM


WORKSHOP = Path(__file__).resolve().parents[1] / "examples" / "workshop.json"


class DemoPresentationTests(unittest.TestCase):
    def test_generic_state_uses_config_without_town_entities(self):
        session = DemoSession(load_world(WORKSHOP))
        state = session.state()
        self.assertEqual(state["scenario"]["name"], "山间工坊")
        self.assertIn("交付矿石", state["scenario"]["description"])
        view = state["scenario"]["presentation"]
        self.assertEqual(view["art"], "generic")
        self.assertEqual(view["default_actor"], "apprentice")
        self.assertNotIn("shortcuts", view)
        self.assertEqual(state["trace_filename"], "loom-workshop-0.jsonl")
        trace = session.step("apprentice", "把矿石交给工匠")
        self.assertEqual(trace["status"], "executed")
        self.assertNotIn("presentation", trace["context"])
        self.assertEqual(session.state()["scenario"], state["scenario"])

    def test_art_requires_scene_structure_not_just_id_or_player(self):
        town = load_world().to_dict()
        view = demo_presentation(town)
        self.assertEqual(view["art"], "lantern-town")
        self.assertEqual(view["default_actor"], "mara")
        self.assertEqual(len(view["shortcuts"]), 6)
        for change in [lambda data: data["locations"]["square"]["connections"].pop(),
                       lambda data: data["rules"].clear(),
                       lambda data: data["facts"]["greeting"].update(text="新故事"),
                       lambda data: data["actors"]["mara"].update(allowed_actions=["move"])]:
            custom = copy.deepcopy(town)
            change(custom)
            self.assertEqual(demo_presentation(custom)["art"], "generic")
        custom = load_world(WORKSHOP).to_dict()
        custom["id"] = "lantern_town"
        self.assertEqual(demo_presentation(custom)["art"], "generic")

    def test_minimal_world_needs_no_player_quests_facts_or_rules(self):
        world = load_world(Path(__file__).with_name("fixtures") / "minimal_world.json")
        state = DemoSession(world).state()
        self.assertEqual(state["scenario"]["name"], world.to_dict()["name"])
        self.assertEqual(state["scenario"]["presentation"]["default_actor"], "resident")

    def test_download_filename_cannot_inject_headers_or_paths(self):
        self.assertEqual(trace_filename({"id": '山间/../工坊\r\n"', "tick": 2}), "loom-world-2.jsonl")
        self.assertEqual(trace_filename(load_world().to_dict()), "loom-lantern-town-0.jsonl")


class CustomDemoHTTPTests(unittest.TestCase):
    def start(self, path):
        self.server = create_server(load_world(WORKSHOP), port=0, session_file=path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            body = None if data is None else json.dumps(data).encode("utf-8")
            connection.request("GET" if data is None else "POST", path, body=body,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            content = response.read().decode("utf-8")
            self.assertEqual(response.status, 200, content)
            return content, response.getheader("Content-Disposition")
        finally:
            connection.close()

    def api(self, path, data=None):
        return json.loads(self.request(path, data)[0])

    def test_workshop_default_actor_restart_restore_reset_and_trace(self):
        path = Path(tempfile.mkdtemp(prefix="loom-custom-demo-")) / "session.jsonl"
        self.start(path)
        try:
            initial = self.api("/api/state")
            rejected = self.api("/api/step", {"input": "进入锻造间"})
            self.assertEqual(rejected["trace"]["actor_id"], "apprentice")
            self.assertEqual(rejected["trace"]["verification"]["code"], "BRIEFING_REQUIRED")
            self.assertEqual(rejected["world"], initial["world"])
            self.api("/api/step", {"input": "把矿石交给工匠"})
            progress = self.api("/api/state")
        finally:
            self.stop()
        with patch.object(MockLLM, "generate_decision", side_effect=AssertionError("Recovery called model")):
            self.start(path)
        try:
            self.assertEqual(self.api("/api/state"), progress)
            self.api("/api/step", {"actor_id": "smith", "input": "请向学徒说明开工计划"})
            final = self.api("/api/step", {"actor_id": "apprentice", "input": "进入锻造间"})
            final.pop("trace")
            self.assertTrue(all(final["world"]["quests"].values()))
            exported, disposition = self.request("/api/trace")
            self.assertEqual(disposition, 'attachment; filename="loom-workshop-3.jsonl"')
            self.assertEqual(final["trace_filename"], "loom-workshop-3.jsonl")
            self.assertEqual(self.api("/api/replay", {"jsonl": exported})["world"], final["world"])
            self.assertEqual(self.api("/api/state"), final)
            self.assertEqual(self.api("/api/reset", {}), initial)
            with patch.object(MockLLM, "generate_decision", side_effect=AssertionError("Restore called model")):
                restored = self.api("/api/restore", {"jsonl": exported})
            restored.pop("ok")
            self.assertEqual(restored, final)
        finally:
            self.stop()
        self.start(path)
        try:
            self.assertEqual(self.api("/api/state"), final)
        finally:
            self.stop()
