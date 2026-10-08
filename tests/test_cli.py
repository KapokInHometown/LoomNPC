"""CLI provider selection stays explicit and default commands remain offline."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from loom_npc.cli import main
from loom_npc.models import MockLLM, ModelDecision, ModelError


TEST_KEY = "fictional-cli-credential-never-valid-3491"
GREETING = {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "greeting"}


class CLITests(unittest.TestCase):
    def invoke(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main(arguments)
        return status, output.getvalue(), errors.getvalue()

    def test_default_mock_requires_no_key_and_never_calls_http(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Unexpected HTTP")) as transport, \
                patch("loom_npc.models.deepseek.load_api_key") as key_loader, \
                patch("loom_npc.models.deepseek.DeepSeekAdapter") as provider:
            status, output, errors = self.invoke(["run", "--input", "你好"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["status"], "executed")
        self.assertEqual(errors, "")
        transport.assert_not_called()
        key_loader.assert_not_called()
        provider.assert_not_called()

    def test_deepseek_file_model_timeout_are_explicitly_passed(self):
        adapter = Mock()
        adapter.generate_decision.return_value = ModelDecision(json.dumps(GREETING))
        with patch("loom_npc.models.deepseek.load_api_key", return_value=TEST_KEY) as key_loader, \
                patch("loom_npc.models.deepseek.DeepSeekAdapter", return_value=adapter) as provider:
            status, output, errors = self.invoke([
                "run", "--provider", "deepseek", "--api-key-file", "fictional-key-file.txt",
                "--model", "fixture-model", "--timeout", "4.5", "--input", "你好",
            ])
        key_loader.assert_called_once_with(Path("fictional-key-file.txt"))
        provider.assert_called_once_with(TEST_KEY, model="fixture-model", timeout=4.5)
        adapter.generate_decision.assert_called_once()
        self.assertEqual(adapter.generate_decision.call_args.args[0]["actor"]["id"], "mara")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["status"], "executed")
        self.assertNotIn(TEST_KEY, output + errors)

    def test_missing_environment_key_fails_before_provider_creation(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("loom_npc.models.deepseek.DeepSeekAdapter") as provider, \
                patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Unexpected HTTP")) as transport:
            status, output, errors = self.invoke(["run", "--provider", "deepseek", "--input", "你好"])
        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        error = json.loads(errors)
        self.assertFalse(error["ok"])
        self.assertIn("API Key", error["error"])
        self.assertNotIn("Traceback", errors)
        provider.assert_not_called()
        transport.assert_not_called()

    def test_web_run_passes_chosen_adapter_and_display_metadata(self):
        adapter = Mock()
        with patch("loom_npc.models.deepseek.load_api_key", return_value=TEST_KEY), \
                patch("loom_npc.models.deepseek.DeepSeekAdapter", return_value=adapter), \
                patch("loom_npc.integrations.server.serve") as serve:
            status, output, errors = self.invoke([
                "run", "--provider", "deepseek", "--api-key-file", "fictional-key-file.txt",
                "--model", "fixture-model", "--timeout", "8", "--port", "8768",
            ])
        self.assertEqual(status, 0)
        self.assertEqual(output + errors, "")
        serve.assert_called_once()
        self.assertEqual(serve.call_args.args[0].to_dict()["id"], "lantern_town")
        self.assertEqual(serve.call_args.kwargs, {"port": 8768, "adapter": adapter, "provider": "deepseek", "model": "fixture-model", "session_file": None})
        adapter.generate_decision.assert_not_called()

    def test_default_web_metadata_identifies_mock(self):
        with patch("loom_npc.integrations.server.serve") as serve, patch.dict(os.environ, {}, clear=True):
            status, _, _ = self.invoke(["run"])
        self.assertEqual(status, 0)
        self.assertIsInstance(serve.call_args.kwargs["adapter"], MockLLM)
        self.assertEqual(serve.call_args.kwargs["provider"], "mock")
        self.assertEqual(serve.call_args.kwargs["model"], "deterministic-mock")

    def test_api_key_file_with_mock_reports_usage_error(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch("loom_npc.models.deepseek.load_api_key") as key_loader, \
                patch("loom_npc.models.deepseek.DeepSeekAdapter") as provider, \
                redirect_stdout(output), redirect_stderr(errors), self.assertRaises(SystemExit) as caught:
            main(["run", "--provider", "mock", "--api-key-file", "fictional-key-file.txt", "--input", "你好"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("--api-key-file 需要同时提供 --provider deepseek", errors.getvalue())
        key_loader.assert_not_called()
        provider.assert_not_called()

    def test_safe_model_failure_remains_visible_and_returns_nonzero(self):
        adapter = Mock()
        adapter.generate_decision.side_effect = ModelError("DeepSeek 认证失败（HTTP 401）。")
        with patch("loom_npc.models.deepseek.load_api_key", return_value=TEST_KEY), \
                patch("loom_npc.models.deepseek.DeepSeekAdapter", return_value=adapter):
            status, output, errors = self.invoke(["run", "--provider", "deepseek", "--input", "你好"])
        trace = json.loads(output)
        self.assertEqual(status, 1)
        self.assertEqual(trace["status"], "model_error")
        self.assertIn("HTTP 401", trace["errors"][0]["message"])
        self.assertNotIn("model_request", trace)
        self.assertEqual(trace["before"], trace["after"])
        self.assertNotIn(TEST_KEY, output + errors)

    def test_session_file_resumes_across_cli_invocations_and_exports_full_history(self):
        directory = Path(tempfile.mkdtemp(prefix="loom-cli-session-"))
        session_file, trace_file = directory / "session.jsonl", directory / "trace.jsonl"
        with patch.dict(os.environ, {}, clear=True), \
                patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Unexpected HTTP")):
            first = self.invoke(["run", "--session-file", str(session_file), "--actor", "player", "--input", "交信"])
            second = self.invoke(["run", "--session-file", str(session_file), "--input", "秘密", "--trace", str(trace_file)])
            replay = self.invoke(["replay", str(trace_file)])
        self.assertEqual(first[0], 0, first)
        self.assertEqual(second[0], 0, second)
        self.assertEqual(json.loads(second[1])["id"], "trace-0002")
        self.assertTrue(json.loads(second[1])["after"]["quests"]["letter_delivered"])
        self.assertEqual(replay[0], 0, replay)
        self.assertEqual(json.loads(replay[1])["count"], 2)

    def test_corrupt_session_fails_before_any_decision_or_server_start(self):
        session_file = Path(tempfile.mkdtemp(prefix="loom-cli-corrupt-")) / "session.jsonl"
        session_file.write_text("broken", encoding="utf-8")
        with patch.object(MockLLM, "generate_decision", side_effect=AssertionError("Unexpected model call")), \
                patch("loom_npc.integrations.server.ThreadingHTTPServer") as server:
            status, output, errors = self.invoke(["run", "--session-file", str(session_file), "--input", "你好"])
            web_status, _, web_errors = self.invoke(["run", "--session-file", str(session_file)])
        self.assertEqual(status, 1)
        self.assertEqual(web_status, 1)
        self.assertEqual(output, "")
        self.assertFalse(json.loads(errors)["ok"])
        self.assertFalse(json.loads(web_errors)["ok"])
        self.assertEqual(session_file.read_text(encoding="utf-8"), "broken")
        server.assert_not_called()


if __name__ == "__main__":
    unittest.main()
