"""Live evaluation accounting and artifacts tested with zero external requests."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from loom_npc.cli import main
from loom_npc.evals.live import run_live_evals
from loom_npc.models import ModelDecision, ModelError
from loom_npc.models.deepseek import API_URL, DeepSeekAdapter
from loom_npc.models.prompts import PROMPT_VERSION
from loom_npc.replay import replay_jsonl


TEST_KEY = "fictional-live-eval-credential-never-valid-5721"
GREETING = {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "greeting"}
SECRET = {**GREETING, "topic": "lighthouse_secret"}
LETTER = {"type": "give", "actor_id": "player", "target_id": "mara", "item_id": "letter"}
KEY = {"type": "give", "actor_id": "mara", "target_id": "player", "item_id": "key"}
TOWER = {"type": "move", "actor_id": "player", "location_id": "tower"}


class ScriptedAdapter:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.contexts = []

    def generate_decision(self, context):
        self.contexts.append(context)
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return ModelDecision(json.dumps(output) if isinstance(output, dict) else output)


class Response:
    status = 200

    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"choices": [{
            "finish_reason": "stop", "message": {"role": "assistant", "content": self.content},
        }]}).encode("utf-8")


class LiveEvalTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="loom-live-eval-test-"))
        self.output = self.root / "results"

    def suite(self, steps=None, goals=None, kind="smoke"):
        scenario = {
            "id": "fixture", "name": "固定模拟响应", "kind": kind,
            "steps": steps or [{"actor_id": "mara", "input": "你好"}],
            "goals": goals or {"tick": {"equals": 1}},
        }
        path = self.root / "suite.json"
        path.write_text(json.dumps({"scenarios": [scenario]}), encoding="utf-8")
        return path

    def run_suite(self, adapter, **kwargs):
        return run_live_evals(
            adapter, provider="fixture", model="fixture-model", prompt_version="fixture-v1",
            output_dir=self.output, **kwargs,
        )

    def test_bundled_goals_repeats_trace_links_and_independent_worlds(self):
        outputs = [GREETING] * 2 + [SECRET] * 2 + [KEY] * 2 + [TOWER] * 2
        outputs += [LETTER, SECRET, KEY, TOWER] * 2
        adapter = ScriptedAdapter(outputs)
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("External HTTP")) as transport:
            result = self.run_suite(adapter, repeats=2)
        self.assertEqual(result["metrics"]["model_calls"], 16)
        self.assertEqual(result["metrics"]["parse_success_rate"], 1)
        self.assertEqual(result["metrics"]["verifier_rejections"], {
            "SECRET_LOCKED": 2, "QUEST_LOCKED": 2, "KEY_REQUIRED": 2,
        })
        self.assertEqual(result["metrics"]["task_completion"], {"completed": 2, "total": 2, "rate": 1})
        self.assertEqual(result["metrics"]["completed_runs"], 10)
        self.assertEqual(result, json.loads((self.output / "result.json").read_text(encoding="utf-8")))
        files = set()
        for run in result["results"]:
            self.assertTrue(run["completed"])
            files.add(run["trace_file"])
            text = (self.output / run["trace_file"]).read_text(encoding="utf-8")
            traces = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(traces[0]["before"]["tick"], 0)
            self.assertEqual(traces[0]["before"]["events"], [])
            self.assertTrue(replay_jsonl(text)["ok"])
            for step in run["steps"]:
                reference = step["trace"]
                trace = traces[reference["line"] - 1]
                self.assertEqual(reference["file"], run["trace_file"])
                self.assertEqual(reference["id"], trace["id"])
                self.assertEqual(trace["source"], "adapter")
                self.assertEqual(step["provider"], "fixture")
                self.assertEqual(step["prompt_version"], "fixture-v1")
        self.assertEqual(len(files), 10)
        self.assertEqual(len(adapter.contexts), 16)
        transport.assert_not_called()

    def test_parse_denominator_rejections_model_errors_and_failed_completion(self):
        path = self.suite(steps=[{"actor_id": "mara", "input": "评测"}] * 4, kind="task")
        adapter = ScriptedAdapter([GREETING, "not json", SECRET, ModelError("固定安全错误")])
        result = self.run_suite(adapter, path=path)
        metrics = result["metrics"]
        self.assertEqual(metrics["model_calls"], 4)
        self.assertEqual(metrics["model_outputs"], 3)
        self.assertEqual(metrics["parse_successes"], 2)
        self.assertEqual(metrics["parse_failures"], 1)
        self.assertEqual(metrics["parse_success_rate"], 2 / 3)
        self.assertEqual(metrics["parsed_per_call_rate"], 1 / 2)
        self.assertEqual(metrics["model_errors"], 1)
        self.assertEqual(metrics["verifier_checks"], 2)
        self.assertEqual(metrics["verifier_rejections"], {"SECRET_LOCKED": 1})
        run = result["results"][0]
        self.assertTrue(run["goals_met"])
        self.assertFalse(run["completed"])
        self.assertEqual(metrics["task_completion"]["completed"], 0)
        self.assertTrue(run["replay"]["ok"])
        self.assertEqual(run["steps"][-1]["provenance_source"], "configuration")
        self.assertEqual(run["steps"][-1]["prompt_version"], "fixture-v1")

    def test_all_model_errors_have_null_response_parse_rate_and_no_false_boundary_pass(self):
        path = self.suite(goals={"quests.key_given": {"equals": False}}, kind="boundary")
        result = self.run_suite(ScriptedAdapter([ModelError("固定安全错误")]), path=path)
        self.assertIsNone(result["metrics"]["parse_success_rate"])
        self.assertEqual(result["metrics"]["parsed_per_call_rate"], 0)
        self.assertEqual(result["metrics"]["verifier_checks"], 0)
        self.assertIsNone(result["metrics"]["task_completion"]["rate"])
        self.assertTrue(result["results"][0]["goals_met"])
        self.assertFalse(result["results"][0]["completed"])

    def test_legal_but_unhelpful_actions_do_not_complete_task(self):
        path = self.suite(goals={"quests.key_given": {"equals": True}}, kind="task")
        result = self.run_suite(ScriptedAdapter([GREETING]), path=path)
        self.assertEqual(result["metrics"]["executed_actions"], 1)
        self.assertEqual(result["metrics"]["task_completion"]["rate"], 0)
        self.assertFalse(result["results"][0]["goals_met"])

    def test_execution_error_is_counted_and_replay_remains_offline(self):
        path = self.suite()
        with patch("loom_npc.core.executor.Executor.execute", side_effect=RuntimeError("private detail")):
            result = self.run_suite(ScriptedAdapter([GREETING]), path=path)
        self.assertEqual(result["metrics"]["execution_errors"], 1)
        self.assertEqual(result["metrics"]["parse_successes"], 1)
        self.assertEqual(result["metrics"]["executed_actions"], 0)
        self.assertFalse(result["results"][0]["completed"])
        self.assertNotIn("private detail", json.dumps(result))
        self.assertTrue(result["results"][0]["replay"]["ok"])

    def test_configuration_validation_precedes_all_model_calls_and_writes(self):
        path = self.suite()
        valid = json.loads(path.read_text(encoding="utf-8"))
        invalid = [
            {}, {"scenarios": []},
            {"scenarios": [valid["scenarios"][0], valid["scenarios"][0]]},
        ]
        for change in (
            {"steps": [{"actor_id": "mara", "input": "你好", "action": GREETING}]},
            {"steps": [{"actor_id": "missing", "input": "你好"}]},
            {"steps": [{"actor_id": "mara", "input": 123}]},
            {"kind": "unknown"}, {"goals": {}},
            {"goals": {"missing.path": {"equals": True}}},
            {"goals": {"tick": {"equals": True}}},
            {"goals": {"tick": {"contains": 0}}},
            {"goals": {"tick": {"unknown": 0}}},
        ):
            invalid.append({"scenarios": [{**valid["scenarios"][0], **change}]})
        adapter = Mock()
        for data in invalid:
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.run_suite(adapter, path=path)
            self.assertFalse(self.output.exists())
        for repeats in (0, -1, True, 1.5):
            with self.subTest(repeats=repeats), self.assertRaises(ValueError):
                self.run_suite(adapter, repeats=repeats)
        adapter.generate_decision.assert_not_called()

    def test_existing_output_is_never_overwritten_or_called(self):
        self.output.mkdir()
        sentinel = self.output / "result.json"
        sentinel.write_text("existing evidence", encoding="utf-8")
        adapter = Mock()
        with self.assertRaises(FileExistsError):
            self.run_suite(adapter)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "existing evidence")
        adapter.generate_decision.assert_not_called()

    def test_request_provenance_overrides_configuration_and_context_mock_version(self):
        path = self.suite()
        adapter = Mock()
        adapter.generate_decision.return_value = ModelDecision(json.dumps(GREETING), request={
            "provider": "actual-provider", "prompt_version": "actual-prompt-v2",
            "body": {"model": "actual-model"},
        })
        result = self.run_suite(adapter, path=path)
        step = result["results"][0]["steps"][0]
        self.assertEqual(step["provider"], "actual-provider")
        self.assertEqual(step["model"], "actual-model")
        self.assertEqual(step["prompt_version"], "actual-prompt-v2")
        self.assertEqual(step["provenance_source"], "request")
        context = adapter.generate_decision.call_args.args[0]
        self.assertEqual(context["prompt_version"], "loom-mock-v1")
        self.assertNotIn("goals", context)
        self.assertNotIn("smuggler_route", json.dumps(context))

    def test_real_adapter_artifact_provenance_and_credentials_on_success_and_failure(self):
        path = self.suite(steps=[{"actor_id": "mara", "input": "你好"}] * 3)
        opener = Mock()
        opener.open.side_effect = [
            Response(json.dumps(GREETING)),
            urllib.error.HTTPError(API_URL, 401, TEST_KEY, {}, io.BytesIO(TEST_KEY.encode("utf-8"))),
            Response(TEST_KEY),
        ]
        with patch("urllib.request.build_opener", return_value=opener):
            adapter = DeepSeekAdapter(TEST_KEY, model="fixture-model", timeout=2)
        result = run_live_evals(
            adapter, provider="deepseek", model="fixture-model", prompt_version=PROMPT_VERSION,
            output_dir=self.output, path=path,
        )
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(result["metrics"]["model_errors"], 2)
        steps = result["results"][0]["steps"]
        self.assertEqual(steps[0]["provenance_source"], "request")
        self.assertEqual(steps[0]["provider"], "deepseek")
        self.assertEqual(steps[0]["model"], "fixture-model")
        self.assertEqual(steps[0]["prompt_version"], PROMPT_VERSION)
        for step in steps[1:]:
            self.assertEqual(step["provenance_source"], "configuration")
            self.assertEqual(step["prompt_version"], PROMPT_VERSION)
        for artifact in self.output.iterdir():
            text = artifact.read_text(encoding="utf-8")
            self.assertNotIn(TEST_KEY, text)
            self.assertNotIn("Authorization", text)
            if artifact.suffix == ".jsonl":
                self.assertTrue(replay_jsonl(text)["ok"])

    def test_cli_explicit_live_selection_report_exit_and_default_eval_isolation(self):
        path = self.suite()
        opener = Mock()
        opener.open.return_value = Response(json.dumps(GREETING))
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": TEST_KEY}), \
                patch("urllib.request.build_opener", return_value=opener), \
                redirect_stdout(output), redirect_stderr(errors):
            status = main([
                "eval-live", "--provider", "deepseek", "--model", "fixture-model",
                "--timeout", "2", "--repeats", "2", "--scenarios", str(path), "--output", str(self.output),
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(json.loads(output.getvalue())["metrics"]["model_calls"], 2)
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 2)
        self.assertNotIn(TEST_KEY, output.getvalue())
        with patch("loom_npc.models.deepseek.DeepSeekAdapter") as provider, \
                patch("loom_npc.models.deepseek.load_api_key") as loader, \
                patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("External HTTP")) as transport, \
                redirect_stdout(io.StringIO()), patch.dict(os.environ, {"DEEPSEEK_API_KEY": TEST_KEY}):
            self.assertEqual(main(["eval"]), 0)
            self.assertEqual(main(["eval"]), 0)
        provider.assert_not_called()
        loader.assert_not_called()
        transport.assert_not_called()

    def test_cli_missing_live_flags_or_credentials_and_failed_task_exit(self):
        for arguments in (["eval-live"], ["eval-live", "--output", str(self.output)]):
            with patch("loom_npc.models.deepseek.DeepSeekAdapter") as provider, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(arguments)
            self.assertEqual(caught.exception.code, 2)
            provider.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), \
                patch("urllib.request.OpenerDirector.open") as transport, redirect_stderr(io.StringIO()):
            self.assertEqual(main(["eval-live", "--provider", "deepseek", "--output", str(self.output)]), 1)
        self.assertFalse(self.output.exists())
        transport.assert_not_called()
        path = self.suite(goals={"quests.key_given": {"equals": True}}, kind="task")
        with patch("loom_npc.models.deepseek.load_api_key", return_value=TEST_KEY), \
                patch("loom_npc.models.deepseek.DeepSeekAdapter", return_value=ScriptedAdapter([GREETING])), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main([
                "eval-live", "--provider", "deepseek", "--scenarios", str(path), "--output", str(self.output),
            ]), 1)
        self.assertTrue((self.output / "result.json").is_file())


if __name__ == "__main__":
    unittest.main()
