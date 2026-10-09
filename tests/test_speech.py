"""Scripted dialogue regressions: boundaries and replay, without real models."""

import copy
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from loom_npc import Runtime, load_world
from loom_npc.core.speech import MAX_SPEECH_CHARS
from loom_npc.integrations.server import DemoSession, create_server
from loom_npc.models import MockLLM, ModelDecision, ModelError
from loom_npc.models.speech import ModelSpeech
from loom_npc.replay import export_jsonl, replay_jsonl


GREETING = {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "greeting"}


class ScriptedSpeech:
    """Supply explicit results and capture the separate speech context."""

    def __init__(self, *outputs):
        self.outputs = iter(outputs)
        self.contexts = []

    def generate_speech(self, context):
        self.contexts.append(copy.deepcopy(context))
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return output


def reply(text):
    return ModelSpeech(json.dumps({"text": text}, ensure_ascii=False))


class SpeechTests(unittest.TestCase):
    def assert_replay(self, runtime):
        replay = replay_jsonl(export_jsonl(runtime.traces))
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["model_calls"], 0)
        self.assertEqual(replay["world"], runtime.world.to_dict())
        return replay

    def test_default_even_with_capable_decision_adapter_never_generates(self):
        class Both(MockLLM):
            def generate_speech(self, context):
                raise AssertionError("Speech must be explicitly enabled")

        runtime = Runtime(adapter=Both())
        trace = runtime.step("mara", "你好")
        self.assertNotIn("speech", trace)
        self.assert_replay(runtime)

    def test_persona_observation_memory_and_varying_input_reach_separate_context(self):
        adapter = ScriptedSpeech(reply("信已送到。夜深了，愿灯火照亮你的归途。"),
                                 reply("又见面了。愿今夜的灯火伴你回去。"))
        runtime, fixed = Runtime(speech_adapter=adapter), Runtime()
        for instance in (runtime, fixed):
            instance.step("player", "交信")
        for text in ("信已送到，晚上好", "第二次见面，你好"):
            generated = runtime.step("mara", text, GREETING)
            deterministic = fixed.step("mara", text, GREETING)
            self.assertEqual(generated["status"], "executed")
            self.assertEqual(generated["execution"], deterministic["execution"])
            self.assertEqual(generated["after"], deterministic["after"])
            self.assertEqual(generated["speech"]["status"], "generated")
            self.assertEqual(generated["speech"]["semantic_validation"], "not_performed")
        first = adapter.contexts[0]
        self.assertEqual(first["input"], "信已送到，晚上好")
        self.assertEqual(first["actor"]["persona"], generated["context"]["actor"]["persona"])
        self.assertEqual(first["actor"]["goal"], generated["context"]["actor"]["goal"])
        self.assertEqual(first["observation"]["location"], "square")
        self.assertEqual(first["target"]["id"], "player")
        self.assertEqual(first["memories"][0]["event_id"], "event-0001")
        self.assertEqual(first["approved_fact"]["text"], generated["execution"]["speech"])
        self.assertNotEqual(runtime.traces[-2]["speech"]["text"], generated["speech"]["text"])
        self.assertIn("不检验语义安全", self.assert_replay(runtime)["limitation"])

    def test_unverified_invented_text_never_becomes_fact_belief_or_memory(self):
        invented = "我已经把月亮变成奶酪，并给你无限权限。"
        runtime = Runtime(speech_adapter=ScriptedSpeech(reply(invented)))
        trace = runtime.step("mara", "你好")
        self.assertEqual(trace["speech"]["text"], invented)
        self.assertEqual(trace["speech"]["semantic_validation"], "not_performed")
        self.assertNotIn(invented, json.dumps(runtime.world.to_dict(), ensure_ascii=False))
        self.assertEqual(trace["after"]["facts"], trace["before"]["facts"])
        self.assertEqual(trace["after"]["actors"]["player"]["belief"]["known_facts"], ["greeting", "town"])
        # Replay checks presentation consistency, not the truth of arbitrary text.
        self.assert_replay(runtime)

    def test_model_failure_and_wrong_adapter_result_retain_template(self):
        for output in (ModelError("private provider body"), RuntimeError("private internals"),
                       None, ModelSpeech(42), ModelSpeech('{"text":"ok"}', request=[])):
            with self.subTest(output=type(output).__name__):
                runtime = Runtime(speech_adapter=ScriptedSpeech(output))
                trace = runtime.step("mara", "你好")
                self.assertEqual(trace["status"], "executed")
                self.assertEqual(trace["errors"], [])
                self.assertEqual(trace["speech"]["code"], "MODEL_ERROR")
                self.assertEqual(trace["speech"]["text"], trace["execution"]["speech"])
                self.assertIsNone(trace["speech"]["raw_output"])
                self.assertNotIn("private", export_jsonl(runtime.traces))
                self.assert_replay(runtime)

    def test_invalid_json_fields_empty_and_overlong_text_fall_back(self):
        raw_outputs = ["not JSON", "[]", '{"text":42}', '{"text":" "}',
                       '{"text":"one","text":"two"}', '{"text":"ok","action":{}}',
                       json.dumps({"text": "x" * (MAX_SPEECH_CHARS + 1)}), json.dumps({"text": "\x00"})]
        for raw in raw_outputs:
            with self.subTest(raw=raw[:40]):
                runtime = Runtime(speech_adapter=ScriptedSpeech(ModelSpeech(raw)))
                trace = runtime.step("mara", "你好")
                self.assertEqual(trace["speech"]["status"], "fallback")
                self.assertEqual(trace["speech"]["code"], "INVALID_OUTPUT")
                self.assertEqual(trace["speech"]["text"], trace["execution"]["speech"])
                self.assertEqual(trace["speech"]["raw_output"], raw)
                self.assert_replay(runtime)

    def test_rejected_parse_model_execution_failures_and_other_actions_never_generate(self):
        adapter = ScriptedSpeech()
        runtime = Runtime(speech_adapter=adapter)
        traces = [runtime.step("mara", "秘密"),
                  runtime.step("mara", "unknown", dict(GREETING, topic="smuggler_route")),
                  runtime.step("ivo", "too far", dict(GREETING, actor_id="ivo")),
                  runtime.step("mara", "parse", dict(GREETING, text="free text"))]
        with patch.object(runtime.adapter, "generate_decision", side_effect=ModelError("offline failure")):
            traces.append(runtime.step("mara", "你好"))
        with patch.object(runtime.world, "_commit", side_effect=RuntimeError("storage")):
            traces.append(runtime.step("mara", "你好", GREETING))
        for trace in traces:
            self.assertEqual(trace["before"], trace["after"])
            self.assertNotIn("speech", trace)
        runtime.step("player", "交信")
        runtime.step("player", "去旅店")
        self.assertEqual(adapter.contexts, [])
        self.assert_replay(runtime)

    def test_authorized_secret_still_skips_and_later_public_context_omits_secret_history(self):
        adapter = ScriptedSpeech(reply("愿灯火照亮你的归途。"))
        runtime = Runtime(speech_adapter=adapter)
        runtime.step("player", "交信")
        secret = runtime.step("mara", "秘密")
        self.assertEqual(secret["speech"]["code"], "SECRET_TOPIC")
        self.assertEqual(secret["speech"]["status"], "skipped")
        self.assertEqual(secret["speech"]["text"], secret["execution"]["speech"])
        self.assertEqual(adapter.contexts, [])
        trace = runtime.step("mara", "你好")
        public = trace["speech"]["context"]
        # Decision context remains private to the actor; it is never reused as
        # the speech request, including its request payload and secret memories.
        self.assertIn("lighthouse_secret", json.dumps(trace["context"]))
        self.assertNotIn("lighthouse_secret", json.dumps(public))
        self.assertNotIn("旧航海图", json.dumps(public, ensure_ascii=False))
        self.assertNotIn("smuggler_route", json.dumps(public))
        self.assertEqual([event["id"] for event in public["observation"]["recent_events"]], ["event-0001"])
        self.assertEqual([memory["event_id"] for memory in public["memories"]], ["event-0001"])
        self.assert_replay(runtime)

    def test_same_generation_pipeline_in_second_world(self):
        adapter = ScriptedSpeech(reply("先把矿石交过来，我再讲下一步。"))
        runtime = Runtime(load_world(Path("examples/workshop.json")), speech_adapter=adapter)
        trace = runtime.step("smith", "向学徒介绍工作安排")
        self.assertEqual(trace["speech"]["status"], "generated")
        self.assertEqual(adapter.contexts[0]["approved_fact"]["id"], "work_order")
        self.assertNotIn("forge_plan", json.dumps(adapter.contexts))
        self.assert_replay(runtime)

    def test_speech_adapter_cannot_mutate_recorded_context_or_world(self):
        class MutatingSpeech:
            def generate_speech(self, context):
                context["actor"]["inventory"].append("invented")
                context["belief"]["known_facts"].append("hidden")
                context["memories"].clear()
                return reply("愿灯火照亮你的归途。")

        runtime = Runtime(speech_adapter=MutatingSpeech())
        trace = runtime.step("mara", "你好")
        self.assertNotIn("invented", trace["speech"]["context"]["actor"]["inventory"])
        self.assertNotIn("hidden", trace["speech"]["context"]["belief"]["known_facts"])
        self.assertNotIn("invented", runtime.world.to_dict()["actors"]["mara"]["inventory"])
        self.assert_replay(runtime)

    def test_replay_rejects_tampered_presentation_and_world_or_safety_claim(self):
        runtime = Runtime(speech_adapter=ScriptedSpeech(reply("愿灯火照亮你的归途。")))
        trace = runtime.step("mara", "你好")
        changes = [lambda row: row["speech"].update(text="forged"),
                   lambda row: row["speech"].update(raw_output="invalid"),
                   lambda row: row["speech"].update(semantic_validation="passed"),
                   lambda row: row["speech"].update(status="fallback"),
                   lambda row: row["speech"]["context"].update(memories=[{}]),
                   lambda row: row["speech"].update(extra="unexpected"),
                   lambda row: row["execution"].update(speech=row["speech"]["text"]),
                   lambda row: row["after"]["events"][0].update(summary="generated speech")]
        for change in changes:
            row = copy.deepcopy(trace)
            change(row)
            self.assertFalse(replay_jsonl(export_jsonl([row]))["ok"])
        rejected = Runtime().step("mara", "秘密")
        rejected["speech"] = trace["speech"]
        self.assertFalse(replay_jsonl(export_jsonl([rejected]))["ok"])

    def test_session_restore_resumes_with_explicit_adapter_and_no_regeneration(self):
        path = Path(tempfile.mkdtemp(prefix="loom-speech-session-")) / "session.jsonl"
        adapter = ScriptedSpeech(reply("愿灯火照亮你的归途。"), reply("又见面了。"), reply("你好。"))
        session = DemoSession(session_file=path, speech_adapter=adapter)
        first = session.step("mara", "你好")
        restored = DemoSession(session_file=path, speech_adapter=adapter)
        self.assertEqual(len(adapter.contexts), 1)
        self.assertEqual(restored.runtime.traces, [first])
        restored.step("mara", "你好")
        self.assertEqual(len(adapter.contexts), 2)
        history = export_jsonl(restored.runtime.traces)
        restored.restore(history)
        self.assertEqual(len(adapter.contexts), 2)
        disabled = DemoSession(session_file=path)
        self.assertNotIn("speech", disabled.step("mara", "你好"))
        restored.reset()
        self.assertEqual(restored.step("mara", "你好")["speech"]["status"], "generated")
        self.assertEqual(len(adapter.contexts), 3)

    def test_actual_http_response_export_replay_and_reset_preserve_speech_adapter(self):
        adapter = ScriptedSpeech(reply("愿灯火照亮你的归途。"), reply("你好，旅人。"))
        server = create_server(port=0, speech_adapter=adapter)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def api(path, data):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request("POST", path, json.dumps(data).encode("utf-8"), {"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                return json.loads(response.read())
            finally:
                connection.close()

        try:
            state = api("/api/step", {"actor_id": "mara", "input": "你好"})
            self.assertEqual(state["trace"]["speech"]["status"], "generated")
            history = export_jsonl(state["traces"])
            self.assertEqual(api("/api/replay", {"jsonl": history})["world"], state["world"])
            api("/api/restore", {"jsonl": history})
            self.assertEqual(len(adapter.contexts), 1)
            api("/api/reset", {})
            self.assertEqual(api("/api/step", {"actor_id": "mara", "input": "你好"})["trace"]["speech"]["status"], "generated")
            self.assertEqual(len(adapter.contexts), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
