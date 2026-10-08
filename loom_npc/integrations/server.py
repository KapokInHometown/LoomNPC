"""Local, single-world HTTP demo using only the Python standard library."""

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Type
from urllib.parse import urlsplit

from loom_npc import Runtime, load_world
from loom_npc.core import WorldState
from loom_npc.evals import run_evals
from loom_npc.models import LLMAdapter
from loom_npc.replay import export_jsonl, replay_jsonl
from loom_npc.replay.session import SessionStore

WEB_ROOT = Path(__file__).with_name("web")
MAX_BODY = 2 * 1024 * 1024


class DemoSession:
    """Own one demo world; serialize requests so traces remain ordered."""

    def __init__(self, world: Optional[WorldState] = None, adapter: Optional[LLMAdapter] = None,
                 provider: str = "mock", model: str = "deterministic-mock",
                 session_file: Optional[Path] = None) -> None:
        self.initial = (world or load_world()).to_dict()
        self.adapter = adapter
        self.provider = provider
        self.model = model
        self.runtime = Runtime(load_world_from_dict(self.initial), adapter=self.adapter)
        self.lock = threading.Lock()
        self.store = SessionStore(session_file, load_world_from_dict(self.initial)) if session_file is not None else None
        if self.store is not None:
            if self.store.path.exists():
                self.runtime = self.store.load(adapter=self.adapter)
            else:
                self.store.save(self.runtime)

    def _commit(self, runtime: Runtime) -> None:
        if self.store is not None:
            self.store.save(runtime)
        self.runtime = runtime

    def step(self, actor_id: str, message: str, action: Any = None) -> Dict[str, Any]:
        """Commit a decision only after saving; caller holds the session lock."""
        candidate = self.runtime
        if self.store is not None:
            candidate = Runtime(load_world_from_dict(self.runtime.world.to_dict()), adapter=self.adapter)
            candidate.traces = copy.deepcopy(self.runtime.traces)
        trace = candidate.step(actor_id, message, proposed_action=action)
        self._commit(candidate)
        return trace

    def reset(self) -> None:
        """Persist an empty initial session; caller holds the session lock."""
        self._commit(Runtime(load_world_from_dict(self.initial), adapter=self.adapter))

    def restore(self, text: str) -> None:
        """Replace the session with validated traces; caller holds the lock."""
        candidate = Runtime.from_jsonl(text, adapter=self.adapter, initial=load_world_from_dict(self.initial))
        self._commit(candidate)

    def save(self) -> None:
        """Explicitly save to the configured path; caller holds the lock."""
        if self.store is None:
            raise ValueError("本地保存需启动时显式指定 --session-file")
        self.store.save(self.runtime)

    def state(self) -> Dict[str, Any]:
        """Expose debugger state, separate from the NPC's filtered context."""
        return {
            "world": self.runtime.world.to_dict(),
            "traces": self.runtime.traces,
            "scenario": {
                "name": "灯港镇的一封信",
                "provider": self.provider,
                "model": self.model,
                "description": "交付信件，建立信任，再打开灯塔。",
                "debugger": True,
            },
        }


def load_world_from_dict(data: Dict[str, Any]) -> WorldState:
    """Reconstruct through the same schema validation as a file load."""
    return WorldState.from_dict(data)


def make_handler(session: DemoSession) -> Type[BaseHTTPRequestHandler]:
    """Bind a session without relying on a process-global mutable world."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # User input and trace content never enter access logs.
            return

        def respond(self, status: int, data: bytes, content_type: str, filename: Optional[str] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def json(self, value: object, status: int = 200) -> None:
            self.respond(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def local_request(self) -> bool:
            """Accept only the bound loopback host and same-origin writes."""
            allowed = {"127.0.0.1:%s" % self.server.server_port, "localhost:%s" % self.server.server_port}
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if host not in allowed or (origin and origin != "http://" + host):
                self.json({"error": "仅接受本地同源请求"}, 403)
                return False
            return True

        def do_GET(self) -> None:
            if not self.local_request():
                return
            path = urlsplit(self.path).path
            if path == "/api/state":
                with session.lock:
                    self.json(session.state())
                return
            if path == "/api/trace":
                with session.lock:
                    data = export_jsonl(session.runtime.traces).encode("utf-8")
                    filename = "loom-lantern-town-%s.jsonl" % session.runtime.world.tick
                self.respond(200, data, "application/x-ndjson; charset=utf-8", filename)
                return
            static = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"), "/app.js": ("app.js", "text/javascript")}
            if path in static:
                name, mime = static[path]
                self.respond(200, (WEB_ROOT / name).read_bytes(), mime + "; charset=utf-8")
                return
            self.json({"error": "未找到此资源"}, 404)

        def do_POST(self) -> None:
            if not self.local_request():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY:
                    raise ValueError("请求正文需为 1 字节至 2 MiB 的 JSON")
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise ValueError("请求必须使用 application/json")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("请求正文必须是 JSON 对象")
                with session.lock:
                    self.dispatch(urlsplit(self.path).path, data)
            except (ValueError, TypeError, KeyError, UnicodeError) as exc:
                self.json({"error": str(exc)}, 400)
            except OSError:
                self.json({"error": "本地会话保存失败，当前会话未提交"}, 500)

        def dispatch(self, path: str, data: Dict[str, Any]) -> None:
            if path == "/api/reset":
                session.reset()
                self.json(session.state())
            elif path == "/api/step":
                actor = data.get("actor_id", "mara")
                message = data.get("input", "")
                action = data.get("action")
                if not isinstance(actor, str) or actor not in session.runtime.world.to_dict()["actors"]:
                    raise ValueError("actor_id 必须是已存在的角色")
                if not isinstance(message, str) or len(message) > 2000:
                    raise ValueError("输入必须是最多 2000 字的文本")
                if action is not None and not isinstance(action, dict):
                    raise ValueError("action 必须是结构化对象")
                trace = session.step(actor, message, action)
                self.json(dict(session.state(), trace=trace))
            elif path == "/api/save":
                session.save()
                self.json({"ok": True, "count": len(session.runtime.traces)})
            elif path == "/api/restore":
                content = data.get("jsonl")
                if not isinstance(content, str):
                    raise ValueError("jsonl 必须是文本")
                session.restore(content)
                self.json(dict(session.state(), ok=True))
            elif path == "/api/eval":
                self.json(run_evals())
            elif path == "/api/replay":
                content = data.get("jsonl")
                if not isinstance(content, str):
                    raise ValueError("jsonl 必须是文本")
                self.json(replay_jsonl(content))
            else:
                self.json({"error": "未找到此接口"}, 404)

    return Handler


def create_server(world: Optional[WorldState] = None, port: int = 8765,
                  adapter: Optional[LLMAdapter] = None, provider: str = "mock",
                  model: str = "deterministic-mock", session_file: Optional[Path] = None) -> ThreadingHTTPServer:
    """Create a loopback-only HTTP server; port 0 is useful for tests."""
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(DemoSession(world, adapter, provider, model, session_file)))


def serve(world: Optional[WorldState] = None, port: int = 8765,
          adapter: Optional[LLMAdapter] = None, provider: str = "mock",
          model: str = "deterministic-mock", session_file: Optional[Path] = None) -> None:
    """Serve the visual demo until interrupted."""
    with create_server(world, port, adapter, provider, model, session_file) as server:
        mode = "离线 Mock" if provider == "mock" else "DeepSeek 在线 · " + model
        print("Loom NPC / 织幕： http://127.0.0.1:%s（%s，Ctrl+C 停止）" % (server.server_port, mode), flush=True)
        server.serve_forever()
