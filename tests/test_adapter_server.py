"""Exercise adapter ownership across the actual loopback HTTP session boundary."""

import http.client
import json
import threading
import unittest

from loom_npc.integrations.server import create_server
from loom_npc.models import MockLLM
from loom_npc.replay import export_jsonl


class RecordingAdapter:
    """Count fake model calls while returning deterministic runtime proposals."""

    def __init__(self):
        self.calls = 0
        self.mock = MockLLM()

    def generate_decision(self, context):
        self.calls += 1
        return self.mock.generate_decision(context)


class AdapterHTTPTests(unittest.TestCase):
    def test_quest_reset_preserves_adapter_and_eval_stays_offline(self):
        adapter = RecordingAdapter()
        server = create_server(port=0, adapter=adapter, provider="deepseek", model="fixture-model")
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
                return json.loads(content)
            finally:
                connection.close()

        try:
            initial = api("/api/state")
            self.assertEqual(initial["scenario"]["provider"], "deepseek")
            self.assertEqual(initial["scenario"]["model"], "fixture-model")
            self.assertEqual(adapter.calls, 0)
            commands = [("mara", "灯塔的秘密"), ("player", "交信"), ("mara", "灯塔的秘密"),
                        ("mara", "把钥匙交给我"), ("player", "进入灯塔")]
            for index, (actor_id, text) in enumerate(commands):
                state = api("/api/step", {"actor_id": actor_id, "input": text})
                self.assertEqual(state["trace"]["status"], "rejected" if index == 0 else "executed")
                self.assertEqual(state["scenario"], initial["scenario"])
            self.assertEqual(adapter.calls, 5)
            self.assertEqual(state["world"]["actors"]["player"]["location"], "tower")
            replay = api("/api/replay", {"jsonl": export_jsonl(state["traces"])})
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(replay["world"], state["world"])
            self.assertEqual(adapter.calls, 5)

            reset = api("/api/reset", {})
            self.assertEqual(reset, initial)
            self.assertEqual(adapter.calls, 5)
            next_state = api("/api/step", {"actor_id": "mara", "input": "你好"})
            self.assertEqual(adapter.calls, 6)
            self.assertEqual(next_state["trace"]["id"], "trace-0001")
            self.assertEqual(next_state["trace"]["status"], "executed")
            self.assertEqual(next_state["scenario"], initial["scenario"])

            before_eval = api("/api/state")
            evaluation = api("/api/eval", {})
            self.assertGreater(evaluation["total"], 0)
            self.assertEqual(evaluation["passed"], evaluation["total"], evaluation)
            self.assertEqual(adapter.calls, 6)
            self.assertEqual(api("/api/state"), before_eval)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
