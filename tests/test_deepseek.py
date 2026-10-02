"""Provider integration is tested offline with fictional credentials and HTTP mocks."""

import copy
import http.client
import io
import json
import os
import traceback
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from loom_npc import Runtime, load_world
from loom_npc.core import build_context
from loom_npc.models.deepseek import API_URL, DeepSeekAdapter, DeepSeekError, load_api_key
from loom_npc.models.prompts import PROMPT_VERSION, build_messages
from loom_npc.replay import export_jsonl, replay_jsonl


TEST_KEY = "fictional-test-credential-never-valid-7926"
GREETING = {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "greeting"}


def envelope(content=None, finish_reason="stop"):
    if content is None:
        content = json.dumps(GREETING)
    return {"choices": [{"finish_reason": finish_reason, "message": {"role": "assistant", "content": content}}]}


class Response(io.BytesIO):
    status = 200

    def __init__(self, data):
        super().__init__(data if isinstance(data, bytes) else json.dumps(data).encode("utf-8"))


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.builder = patch("loom_npc.models.deepseek.urllib.request.build_opener").start()
        self.addCleanup(patch.stopall)
        self.opener = self.builder.return_value
        self.adapter = DeepSeekAdapter(TEST_KEY)
        self.context = build_context(load_world(), "mara", "你好")

    def respond(self, data):
        self.opener.open.return_value = Response(data)

    def test_request_uses_fixed_host_json_mode_and_actor_context(self):
        self.respond(envelope())
        before = copy.deepcopy(self.context)
        decision = self.adapter.generate_decision(self.context)
        self.assertEqual(json.loads(decision.raw_output), GREETING)
        self.assertEqual(self.context, before)
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TEST_KEY)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(self.opener.open.call_args.kwargs, {"timeout": 30})
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["messages"], build_messages(before))
        self.assertEqual(decision.request, {"provider": "deepseek", "prompt_version": PROMPT_VERSION, "body": payload})
        self.assertNotIn("Authorization", json.dumps(decision.request))
        self.assertNotIn(TEST_KEY, json.dumps(decision.request))
        body = request.data.decode("utf-8")
        self.assertNotIn(TEST_KEY, body)
        self.assertNotIn("smuggler_route", body)
        self.assertNotIn("北侧暗礁", body)
        self.assertNotIn(TEST_KEY, repr(self.adapter))

    def test_prompt_schemas_do_not_include_unknown_fact_examples(self):
        context = build_context(load_world(), "player", "你好")
        messages = build_messages(context)
        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertIn(PROMPT_VERSION, messages[0]["content"])
        self.assertIn('Current actor_id (JSON string): "player"', messages[0]["content"])
        schemas = [json.loads(line) for line in messages[0]["content"].splitlines() if line.startswith('{"type"')]
        self.assertEqual([set(schema) for schema in schemas], [
            {"type", "actor_id", "target_id", "topic"},
            {"type", "actor_id", "location_id"},
            {"type", "actor_id", "target_id", "item_id"},
        ])
        self.assertNotIn("lighthouse_secret", json.dumps(messages))
        self.assertNotIn("旧航海图", json.dumps(messages, ensure_ascii=False))
        self.assertEqual(json.loads(messages[1]["content"]), context)

    def test_explicit_model_timeout_and_token_limit_are_sent(self):
        adapter = DeepSeekAdapter(TEST_KEY, model="configured-model", timeout=4.5, max_tokens=200)
        self.respond(envelope())
        adapter.generate_decision(self.context)
        request = self.opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "configured-model")
        self.assertEqual(payload["max_tokens"], 200)
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 4.5)

    def test_redirect_handler_does_not_create_forwarded_request(self):
        handler = self.builder.call_args.args[0]
        request = Mock()
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                redirected = handler.redirect_request(request, None, code, "redirect", {}, "https://other.example/")
                self.assertIsNone(redirected)
        request.assert_not_called()

    def test_invalid_configuration_fails_before_network_access(self):
        for timeout in (0, -1, float("inf"), float("nan"), True, "30"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                DeepSeekAdapter(TEST_KEY, timeout=timeout)
        for tokens in (0, -1, True, 12.5, "512"):
            with self.subTest(tokens=tokens), self.assertRaises(ValueError):
                DeepSeekAdapter(TEST_KEY, max_tokens=tokens)
        for model in ("", " ", "two names", None):
            with self.subTest(model=model), self.assertRaises(ValueError):
                DeepSeekAdapter(TEST_KEY, model=model)
        for key in ("", "XXXX", "sk-xxxx", "bad key", "bad\nkey", "bad\x00key", None):
            with self.subTest(key=key), self.assertRaises(ValueError):
                DeepSeekAdapter(key)
        self.opener.open.assert_not_called()

    def test_timeout_transport_and_http_errors_are_safe_without_retry(self):
        failures = [
            TimeoutError(TEST_KEY),
            urllib.error.URLError(TEST_KEY),
            http.client.IncompleteRead(TEST_KEY.encode("utf-8")),
        ]
        failures += [urllib.error.HTTPError(API_URL, status, TEST_KEY, {"Authorization": TEST_KEY}, io.BytesIO(TEST_KEY.encode("utf-8"))) for status in (301, 401, 429, 500)]
        for failure in failures:
            self.opener.open.reset_mock()
            self.opener.open.side_effect = failure
            with self.subTest(failure=type(failure).__name__):
                try:
                    self.adapter.generate_decision(self.context)
                except DeepSeekError as error:
                    self.assertNotIn(TEST_KEY, str(error))
                    self.assertNotIn(TEST_KEY, "".join(traceback.format_exception(type(error), error, error.__traceback__)))
                else:
                    self.fail("Expected a safe provider error")
                self.opener.open.assert_called_once()

    def test_http_errors_report_fixed_chinese_categories(self):
        for status, category in ((401, "认证失败"), (402, "账户余额不足"), (429, "请求受限"), (302, "重定向已拒绝")):
            self.opener.open.side_effect = urllib.error.HTTPError(API_URL, status, TEST_KEY, {}, io.BytesIO(TEST_KEY.encode("utf-8")))
            with self.subTest(status=status), self.assertRaises(DeepSeekError) as caught:
                self.adapter.generate_decision(self.context)
            self.assertIn(category, str(caught.exception))
            self.assertIn("HTTP " + str(status), str(caught.exception))
            self.assertNotIn(TEST_KEY, str(caught.exception))

    def test_empty_truncated_and_malformed_response_envelopes_are_explicit(self):
        malformed = [
            b"", b"not json", b"\xff", [], {}, {"choices": []},
            {"choices": [None]}, {"choices": [envelope()["choices"][0], envelope()["choices"][0]]},
            envelope("", "stop"), envelope("   ", "stop"), envelope("{}", "length"),
            envelope("{}", "content_filter"), envelope([], "stop"), envelope({}, "stop"),
            {"choices": [{"finish_reason": "stop", "message": None}]},
            {"choices": [{"finish_reason": "stop", "message": {"role": "user", "content": "{}"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": None}}]},
            envelope(TEST_KEY),
        ]
        tool_response = envelope()
        tool_response["choices"][0]["message"]["tool_calls"] = [{"id": "unsupported"}]
        malformed.append(tool_response)
        for value in malformed:
            self.respond(value)
            with self.subTest(value_type=type(value).__name__), self.assertRaises(DeepSeekError) as caught:
                self.adapter.generate_decision(self.context)
            self.assertNotIn(TEST_KEY, str(caught.exception))

    def test_invalid_action_text_still_uses_runtime_parser(self):
        self.respond(envelope("not an action json"))
        runtime = Runtime(adapter=self.adapter)
        trace = runtime.step("mara", "你好")
        self.assertEqual(trace["status"], "parse_error")
        self.assertEqual(trace["state_diff"], [])
        self.assertTrue(replay_jsonl(export_jsonl(runtime.traces))["ok"])

    def test_secret_echo_and_http_failure_never_enter_trace(self):
        runtime = Runtime(adapter=self.adapter)
        self.respond(envelope(TEST_KEY))
        trace = runtime.step("mara", "你好")
        self.assertEqual(trace["status"], "model_error")
        self.assertIsNone(trace["raw_output"])
        self.assertNotIn("model_request", trace)
        self.opener.open.side_effect = urllib.error.HTTPError(API_URL, 401, TEST_KEY, {}, io.BytesIO(TEST_KEY.encode("utf-8")))
        runtime.step("mara", "你好")
        exported = export_jsonl(runtime.traces)
        self.assertNotIn(TEST_KEY, exported)
        self.assertTrue(replay_jsonl(exported)["ok"])

    def test_unicode_escaped_credentials_in_json_keys_and_values_are_not_recorded(self):
        escaped_key = "".join("\\u{:04x}".format(ord(char)) for char in TEST_KEY)
        contents = [
            '{"type":"speak","actor_id":"mara","target_id":"player","topic":"' + escaped_key + '"}',
            '{"type":"speak","actor_id":"mara","target_id":"player","topic":"' + escaped_key + '","topic":"greeting"}',
            '{"' + escaped_key + '":"value"}',
            '{"nested":[{"value":"' + escaped_key + '"}]}',
        ]
        runtime = Runtime(adapter=self.adapter)
        for content in contents:
            self.respond(envelope(content))
            trace = runtime.step("mara", "你好")
            self.assertEqual(trace["status"], "model_error")
            self.assertIsNone(trace["raw_output"])
            self.assertIsNone(trace["action"])
            self.assertNotIn("model_request", trace)
        exported = export_jsonl(runtime.traces)
        self.assertNotIn(TEST_KEY, exported)
        self.assertNotIn(escaped_key, exported)
        self.assertTrue(replay_jsonl(exported)["ok"])

    def test_deepseek_proposals_complete_quest_and_replay_without_network(self):
        actions = [
            {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "lighthouse_secret"},
            {"type": "give", "actor_id": "player", "target_id": "mara", "item_id": "letter"},
            {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "lighthouse_secret"},
            {"type": "give", "actor_id": "mara", "target_id": "player", "item_id": "key"},
            {"type": "move", "actor_id": "player", "location_id": "tower"},
        ]
        self.opener.open.side_effect = [Response(envelope(json.dumps(action))) for action in actions]
        runtime = Runtime(adapter=self.adapter)
        for action in actions:
            runtime.step(action["actor_id"], "固定意图输入")
        self.assertEqual([trace["status"] for trace in runtime.traces], ["rejected", "executed", "executed", "executed", "executed"])
        self.assertEqual(runtime.world.to_dict()["actors"]["player"]["location"], "tower")
        for trace in runtime.traces:
            request = trace["model_request"]
            self.assertEqual(request["provider"], "deepseek")
            self.assertEqual(request["prompt_version"], PROMPT_VERSION)
            self.assertEqual(json.loads(request["body"]["messages"][1]["content"]), trace["context"])
            self.assertNotIn("Authorization", json.dumps(request))
        exported = export_jsonl(runtime.traces)
        self.assertNotIn(TEST_KEY, exported)
        self.opener.open.reset_mock()
        self.opener.open.side_effect = AssertionError("Replay must not call provider")
        replay = replay_jsonl(exported)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["world"], runtime.world.to_dict())
        self.assertEqual(replay["model_calls"], 0)
        self.opener.open.assert_not_called()


class KeyLoadingTests(unittest.TestCase):
    def test_environment_is_only_default_source(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": TEST_KEY}), patch.object(Path, "read_text") as reader:
            self.assertEqual(load_api_key(), TEST_KEY)
            reader.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            load_api_key()

    def test_explicit_file_uses_utf8_first_line_and_overrides_environment(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "environment-value"}), patch.object(Path, "read_text", return_value="Deepseek-API-Key = " + TEST_KEY + "\nignored note") as reader:
            self.assertEqual(load_api_key(Path("explicit-key-file.txt")), TEST_KEY)
            reader.assert_called_once_with(encoding="utf-8")

    def test_malformed_missing_placeholder_file_fails_without_fallback(self):
        malformed = ["", "\nDeepseek-API-Key = " + TEST_KEY, "other = " + TEST_KEY,
                     "Deepseek-API-Key", "Deepseek-API-Key = ", "Deepseek-API-Key = XXXX",
                     "Deepseek-API-Key = bad key", "\ufeffDeepseek-API-Key = " + TEST_KEY]
        for text in malformed:
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": TEST_KEY}), patch.object(Path, "read_text", return_value=text), self.assertRaises(ValueError) as caught:
                load_api_key(Path("explicit-key-file.txt"))
            self.assertNotIn(TEST_KEY, str(caught.exception))
        for failure in (OSError(TEST_KEY), UnicodeError(TEST_KEY)):
            with patch.object(Path, "read_text", side_effect=failure), self.assertRaises(ValueError) as caught:
                load_api_key(Path("explicit-key-file.txt"))
            self.assertNotIn(TEST_KEY, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
