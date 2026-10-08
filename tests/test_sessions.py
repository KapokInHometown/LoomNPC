"""Local recovery recomputes the whole history before committing a session."""

import copy
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from loom_npc import Runtime, load_world
from loom_npc.core import WorldState
from loom_npc.integrations.server import DemoSession, create_server
from loom_npc.models import MockLLM
from loom_npc.replay import export_jsonl, replay_jsonl
from loom_npc.replay.session import SessionStore


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="loom-session-test-")) / "session.jsonl"

    def quest_start(self):
        runtime = Runtime()
        runtime.step("mara", "秘密")  # A rejection must still consume a trace id.
        runtime.step("player", "交信")
        runtime.step("mara", "秘密")
        return runtime

    def test_save_load_continue_and_replay_recompute_identical_world(self):
        original = self.quest_start()
        store = SessionStore(self.path, load_world())
        store.save(original)
        adapter = MockLLM()
        with patch.object(MockLLM, "generate_decision", side_effect=AssertionError("Recovery called model")):
            restored = store.load(adapter=adapter)
        self.assertIs(restored.adapter, adapter)
        self.assertEqual(restored.traces, original.traces)
        self.assertEqual(restored.world.to_dict(), original.world.to_dict())
        # Equality includes belief, trust, events, memories, inventory and quests.
        for actor, message in [("mara", "钥匙"), ("player", "进入灯塔")]:
            next_trace = restored.step(actor, message)
            expected = original.step(actor, message)
            self.assertEqual(next_trace, expected)
        self.assertEqual(next_trace["id"], "trace-0005")
        self.assertEqual(restored.world.tick, 4)
        self.assertEqual(restored.world.actors["player"].location, "tower")
        store.save(restored)
        again = store.load()
        replay = replay_jsonl(export_jsonl(again.traces))
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["world"], original.world.to_dict())
        self.assertEqual(replay["count"], 5)

    def test_empty_session_and_reset_survive_restart(self):
        session = DemoSession(session_file=self.path)
        self.assertEqual(DemoSession(session_file=self.path).state(), session.state())
        session.step("player", "交信")
        session.reset()
        restarted = DemoSession(session_file=self.path)
        self.assertEqual(restarted.runtime.world.to_dict(), load_world().to_dict())
        self.assertEqual(restarted.runtime.traces, [])
        self.assertEqual(restarted.step("mara", "你好")["id"], "trace-0001")

    def test_runtime_restoration_is_detached_and_failure_does_not_touch_source(self):
        original = self.quest_start()
        before = copy.deepcopy(original.traces)
        restored = Runtime.from_jsonl(export_jsonl(before), initial=load_world())
        restored.traces[0]["input"] = "changed"
        self.assertEqual(original.traces, before)
        damaged = copy.deepcopy(before)
        damaged[-1]["after"]["quests"]["letter_delivered"] = False
        with self.assertRaises(ValueError):
            Runtime.from_jsonl(export_jsonl(damaged))
        self.assertEqual(original.traces, before)

    def test_corruption_truncation_and_foreign_world_never_replace_live_session(self):
        session = DemoSession(session_file=self.path)
        session.restore(export_jsonl(self.quest_start().traces))
        before, saved = copy.deepcopy(session.state()), self.path.read_bytes()
        rows = copy.deepcopy(session.runtime.traces)
        variants = []
        for field, value in [("id", "trace-0099"), ("tick", 99), ("state_diff", []), ("actor_id", [])]:
            damaged = copy.deepcopy(rows)
            damaged[-1][field] = value
            variants.append(export_jsonl(damaged))
        damaged = copy.deepcopy(rows)
        damaged[-1]["after"]["actors"]["player"]["memories"] = []
        variants.append(export_jsonl(damaged))
        foreign_world = load_world().to_dict()
        foreign_world["name"] = "another scenario"
        foreign = Runtime(WorldState.from_dict(foreign_world))
        foreign.step("mara", "你好")
        variants.extend([export_jsonl(foreign.traces), "", '{"id":', export_jsonl(rows) + "broken\n"])
        for text in variants:
            with self.subTest(text=text[:50]), self.assertRaises(ValueError):
                session.restore(text)
            self.assertEqual(session.state(), before)
            self.assertEqual(self.path.read_bytes(), saved)
        # Even truncation on a complete line is detected by the file header.
        lines = saved.decode("utf-8").splitlines()
        for content in ["\n".join(lines[:-1]), saved.decode("utf-8")[:-20], "", "[]", saved.decode("utf-8") + "broken\n"]:
            self.path.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                DemoSession(session_file=self.path)
            self.assertEqual(self.path.read_text(encoding="utf-8"), content)
            self.assertEqual(session.state(), before)

    def test_header_version_count_and_initial_world_are_checked(self):
        session = DemoSession(session_file=self.path)
        session.step("mara", "你好")
        lines = self.path.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        variants = [{}, dict(header, version=2), dict(header, version=True),
                    dict(header, trace_count=True), dict(header, trace_count=-1),
                    dict(header, trace_count=0), dict(header, extra="unknown")]
        wrong_initial = copy.deepcopy(header)
        wrong_initial["initial"]["name"] = "another scenario"
        variants.append(wrong_initial)
        for item in variants:
            self.path.write_text(json.dumps(item) + "\n" + "\n".join(lines[1:]), encoding="utf-8")
            with self.assertRaises(ValueError):
                DemoSession(session_file=self.path)

    def test_atomic_save_failure_leaves_both_memory_and_previous_file_intact(self):
        session = DemoSession(session_file=self.path)
        session.step("player", "交信")
        before, saved = copy.deepcopy(session.state()), self.path.read_bytes()
        restored_text = export_jsonl(self.quest_start().traces)
        for failure in ["os.replace", "os.fsync"]:
            for operation in [lambda: session.step("mara", "秘密"), session.reset,
                              lambda: session.restore(restored_text), session.save]:
                with patch("loom_npc.replay.session." + failure, side_effect=OSError("fictional disk failure")), \
                        self.assertRaises(OSError):
                    operation()
                self.assertEqual(session.state(), before)
                self.assertEqual(self.path.read_bytes(), saved)
        # Leftover temporary files are never recovery candidates.
        self.assertEqual(DemoSession(session_file=self.path).state(), before)

    def test_save_refuses_unrecorded_world_mutation(self):
        original = self.quest_start()
        store = SessionStore(self.path, load_world())
        store.save(original)
        saved = self.path.read_bytes()
        original.world = load_world()
        with self.assertRaises(ValueError):
            store.save(original)
        self.assertEqual(self.path.read_bytes(), saved)

    def test_default_session_has_no_filesystem_calls(self):
        with patch.object(SessionStore, "load", side_effect=AssertionError("Unexpected load")), \
                patch.object(SessionStore, "save", side_effect=AssertionError("Unexpected save")):
            session = DemoSession()
            session.step("mara", "你好")
            session.restore(export_jsonl(self.quest_start().traces))
            session.reset()
            with self.assertRaises(ValueError):
                session.save()

    def test_failure_traces_are_preserved_and_numbering_continues(self):
        with patch.object(MockLLM, "generate_decision", side_effect=RuntimeError("fictional failure")):
            session = DemoSession(session_file=self.path)
            self.assertEqual(session.step("mara", "你好")["status"], "model_error")
        self.assertEqual(session.step("mara", "bad", {"type": "unknown"})["status"], "parse_error")
        restarted = DemoSession(session_file=self.path)
        self.assertEqual(restarted.state(), session.state())
        self.assertEqual(restarted.runtime.world.tick, 0)
        trace = restarted.step("mara", "你好")
        self.assertEqual(trace["id"], "trace-0003")
        self.assertEqual(trace["tick"], 0)
        self.assertTrue(replay_jsonl(export_jsonl(restarted.runtime.traces))["ok"])

    def test_separate_processes_resume_the_same_saved_session(self):
        root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment.pop("DEEPSEEK_API_KEY", None)
        environment["PYTHONPATH"] = str(root)
        trace_path = self.path.with_name("export.jsonl")
        commands = [
            ["run", "--session-file", str(self.path), "--actor", "player", "--input", "交信"],
            ["run", "--session-file", str(self.path), "--input", "秘密", "--trace", str(trace_path)],
            ["replay", str(trace_path)],
        ]
        outputs = []
        for command in commands:
            process = subprocess.run([sys.executable, "-m", "loom_npc"] + command,
                                     cwd=root, env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            outputs.append(json.loads(process.stdout))
        self.assertEqual(outputs[1]["id"], "trace-0002")
        self.assertTrue(outputs[2]["ok"])
        self.assertEqual(outputs[2]["count"], 2)
        self.assertEqual(outputs[2]["world"], outputs[1]["after"])


class SessionHTTPTests(unittest.TestCase):
    def start(self, path):
        self.server = create_server(port=0, session_file=path)
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
            return response.status, content
        finally:
            connection.close()

    def api(self, path, data=None):
        status, content = self.request(path, data)
        self.assertEqual(status, 200, content)
        return json.loads(content)

    def test_actual_http_restart_restore_continue_and_corruption_rejection(self):
        path = Path(tempfile.mkdtemp(prefix="loom-http-session-")) / "session.jsonl"
        self.start(path)
        try:
            self.api("/api/step", {"actor_id": "mara", "input": "秘密"})
            before = self.api("/api/step", {"actor_id": "player", "input": "交信"})
            before.pop("trace")
            self.assertEqual(self.api("/api/save", {}), {"ok": True, "count": 2})
        finally:
            self.stop()
        with patch.object(MockLLM, "generate_decision", side_effect=AssertionError("Recovery called model")):
            self.start(path)
        try:
            self.assertEqual(self.api("/api/state"), before)
            next_state = self.api("/api/step", {"input": "秘密"})
            self.assertEqual(next_state["trace"]["id"], "trace-0003")
            _, exported = self.request("/api/trace")
            replay = self.api("/api/replay", {"jsonl": exported})
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(replay["world"], next_state["world"])
            self.api("/api/reset", {})
            empty = self.api("/api/state")
            self.api("/api/replay", {"jsonl": exported})
            self.assertEqual(self.api("/api/state"), empty)
            with patch.object(MockLLM, "generate_decision", side_effect=AssertionError("Restore called model")):
                restored = self.api("/api/restore", {"jsonl": exported})
            restored.pop("ok")
            saved = path.read_bytes()
            for content in ["broken", exported + "broken\n"]:
                status, body = self.request("/api/restore", {"jsonl": content})
                self.assertEqual(status, 400, body)
                self.assertEqual(self.api("/api/state"), restored)
                self.assertEqual(path.read_bytes(), saved)
            with patch("loom_npc.replay.session.os.replace", side_effect=OSError("disk failure")):
                status, body = self.request("/api/step", {"input": "钥匙"})
            self.assertEqual(status, 500, body)
            self.assertEqual(self.api("/api/state"), restored)
            self.assertEqual(path.read_bytes(), saved)
            self.api("/api/step", {"input": "钥匙"})
            final = self.api("/api/step", {"actor_id": "player", "input": "进入灯塔"})
            self.assertEqual(final["trace"]["id"], "trace-0005")
            self.assertEqual(final["world"]["actors"]["player"]["location"], "tower")
            _, exported = self.request("/api/trace")
            self.assertEqual(self.api("/api/replay", {"jsonl": exported})["world"], final["world"])
            final.pop("trace")
        finally:
            self.stop()
        self.start(path)
        try:
            self.assertEqual(self.api("/api/state"), final)
        finally:
            self.stop()


if __name__ == "__main__":
    unittest.main()
