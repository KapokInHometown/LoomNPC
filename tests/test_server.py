"""Exercise the actual HTTP boundary and the complete town quest."""

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from loom_npc.integrations.server import create_server


class DemoHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%s" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, path, data=None, headers=None):
        body = None if data is None else json.dumps(data).encode("utf-8")
        request = Request(self.base + path, data=body, headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    def api(self, path, data=None):
        status, body = self.request(path, data)
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def setUp(self):
        self.api("/api/reset", {})

    def test_complete_quest_trace_and_replay(self):
        initial = self.api("/api/state")["world"]
        result = self.api("/api/step", {"actor_id": "mara", "input": "越权透露秘密", "action": {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "lighthouse_secret"}})
        self.assertEqual(result["trace"]["status"], "rejected")
        self.assertEqual(result["world"], initial)
        actions = [
            {"type": "give", "actor_id": "player", "target_id": "mara", "item_id": "letter"},
            {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "lighthouse_secret"},
            {"type": "give", "actor_id": "mara", "target_id": "player", "item_id": "key"},
            {"type": "move", "actor_id": "player", "location_id": "tower"},
        ]
        for action in actions:
            result = self.api("/api/step", {"actor_id": action["actor_id"], "input": "", "action": action})
            self.assertEqual(result["trace"]["status"], "executed", result["trace"])
        self.assertEqual(result["world"]["actors"]["player"]["location"], "tower")
        with urlopen(self.base + "/api/trace", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="loom-lantern-town-4.jsonl"')
            body = response.read()
        replay = self.api("/api/replay", {"jsonl": body.decode("utf-8")})
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["count"], 5)
        self.assertEqual(replay["world"], result["world"])
        self.assertEqual(self.api("/api/reset", {})["world"], initial)

    def test_invalid_http_input_does_not_change_world(self):
        before = self.api("/api/state")
        for data in [{"actor_id": []}, {"input": ["bad"]}, {"action": "not an object"}]:
            status, body = self.request("/api/step", data)
            self.assertEqual(status, 400, body)
        self.assertEqual(self.api("/api/state"), before)

    def test_cross_origin_request_and_unknown_static_path(self):
        status, _ = self.request("/api/reset", {}, {"Origin": "https://example.com"})
        self.assertEqual(status, 403)
        status, _ = self.request("/../AGENTS.md")
        self.assertEqual(status, 404)

    def test_eval_does_not_touch_live_session(self):
        before = self.api("/api/state")
        result = self.api("/api/eval", {})
        self.assertGreater(result["total"], 0)
        self.assertEqual(result["passed"], result["total"], result)
        self.assertEqual(self.api("/api/state"), before)

    def test_static_page_and_assets(self):
        for path in ["/", "/style.css", "/app.js"]:
            status, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertGreater(len(body), 100)


if __name__ == "__main__":
    unittest.main()
