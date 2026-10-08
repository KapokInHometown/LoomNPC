"""Small command-line entrypoints for the runtime and local demo."""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from loom_npc import Runtime, load_world
from loom_npc.evals import run_evals
from loom_npc.models import MockLLM
from loom_npc.replay import export_jsonl, replay_jsonl


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Execute a CLI command and return an explicit process status."""
    parser = argparse.ArgumentParser(prog="loom-npc", description="Loom NPC / 织幕：可观察的游戏角色运行时")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="启动本地可视化 Demo，或执行一次决策；默认离线 Mock")
    run.add_argument("--port", type=int, default=8765)
    run.add_argument("--world", type=Path)
    run.add_argument("--input", help="执行一次所选模型的决策并输出 trace，不启动网页")
    run.add_argument("--actor", default="mara")
    run.add_argument("--trace", type=Path, help="单次决策时保存 JSONL trace")
    run.add_argument("--session-file", type=Path, help="显式启用本地会话：启动校验恢复，每次决策原子保存")
    run.add_argument("--provider", choices=("mock", "deepseek"), default="mock")
    run.add_argument("--model", default="deepseek-flash", help="DeepSeek 模型名")
    run.add_argument("--api-key-file", type=Path, help="显式读取本地密钥文件；否则使用 DEEPSEEK_API_KEY")
    run.add_argument("--timeout", type=float, default=30, help="DeepSeek 网络超时秒数，默认 30")
    commands.add_parser("eval", help="执行全部固定离线行为评测")
    live = commands.add_parser("eval-live", help="显式调用真实模型评测行为，并保存结果与逐轮 trace")
    live.add_argument("--provider", choices=("deepseek",), required=True)
    live.add_argument("--model", default="deepseek-flash")
    live.add_argument("--api-key-file", type=Path, help="否则使用 DEEPSEEK_API_KEY")
    live.add_argument("--timeout", type=float, default=30)
    live.add_argument("--repeats", type=int, default=1, help="每个场景从初始世界独立重复的次数")
    live.add_argument("--scenarios", type=Path, help="自定义真实评测 JSON；默认随包场景")
    live.add_argument("--output", type=Path, required=True, help="尚不存在的结果目录")
    validate = commands.add_parser("validate", help="校验世界配置")
    validate.add_argument("world", type=Path, nargs="?")
    replay = commands.add_parser("replay", help="不调用模型，校验并回放 JSONL trace")
    replay.add_argument("trace", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "eval":
            result = run_evals()
            _print(result)
            return 0 if result["passed"] == result["total"] else 1
        if args.command == "eval-live":
            from loom_npc.evals.live import run_live_evals
            from loom_npc.models.deepseek import DeepSeekAdapter, load_api_key
            from loom_npc.models.prompts import PROMPT_VERSION

            adapter = DeepSeekAdapter(load_api_key(args.api_key_file), model=args.model, timeout=args.timeout)
            result = run_live_evals(
                adapter, provider=args.provider, model=args.model, prompt_version=PROMPT_VERSION,
                output_dir=args.output, repeats=args.repeats, path=args.scenarios,
            )
            _print(result)
            metrics = result["metrics"]
            return 0 if metrics["completed_runs"] == metrics["total_runs"] else 1
        if args.command == "validate":
            world = load_world(args.world)
            _print({"ok": True, "world": world.to_dict()["name"], "message": "世界配置有效"})
            return 0
        if args.command == "replay":
            result = replay_jsonl(args.trace.read_text(encoding="utf-8"))
            _print(result)
            return 0 if result["ok"] else 1
        world = load_world(args.world)
        if args.trace and args.input is None:
            parser.error("--trace 需要同时提供 --input；网页可通过导出按钮保存 trace")
        if args.api_key_file and args.provider != "deepseek":
            parser.error("--api-key-file 需要同时提供 --provider deepseek")
        adapter = MockLLM()
        model_name = "deterministic-mock"
        if args.provider == "deepseek":
            from loom_npc.models.deepseek import DeepSeekAdapter, load_api_key

            adapter = DeepSeekAdapter(load_api_key(args.api_key_file), model=args.model, timeout=args.timeout)
            model_name = args.model
        if args.input is not None:
            if args.session_file is not None:
                from loom_npc.integrations.server import DemoSession

                session = DemoSession(world, adapter=adapter, session_file=args.session_file)
                with session.lock:
                    trace = session.step(args.actor, args.input)
                runtime = session.runtime
            else:
                runtime = Runtime(world, adapter=adapter)
                trace = runtime.step(args.actor, args.input)
            if args.trace:
                args.trace.parent.mkdir(parents=True, exist_ok=True)
                args.trace.write_text(export_jsonl(runtime.traces), encoding="utf-8")
            _print(trace)
            return 0 if trace["status"] == "executed" else 1
        from loom_npc.integrations.server import serve

        serve(world, port=args.port, adapter=adapter, provider=args.provider, model=model_name,
              session_file=args.session_file)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))
