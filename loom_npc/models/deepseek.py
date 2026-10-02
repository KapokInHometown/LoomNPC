"""Explicit, non-streaming DeepSeek access without an SDK or automatic retries."""

import http.client
import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from . import ModelDecision, ModelError
from .prompts import PROMPT_VERSION, build_messages


API_URL = "https://api.deepseek.com/chat/completions"


class DeepSeekError(ModelError):
    """A safe provider failure message containing no response or credential data."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject redirects so Authorization is never forwarded to another URL."""
        return None


def _validate_key(value: Any) -> str:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ValueError("DeepSeek API Key 必须非空且不含空白字符。")
    placeholder = value.casefold().removeprefix("sk-")
    if set(placeholder) == {"x"} or placeholder in {"your-api-key", "your_api_key", "<your-api-key>", "<api-key>"}:
        raise ValueError("DeepSeek API Key 不能使用示例占位值。")
    if any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ValueError("DeepSeek API Key 含有无效的请求头字符。")
    return value


def load_api_key(key_file: Optional[Path] = None) -> str:
    """Read an explicitly supplied file, or only DEEPSEEK_API_KEY when omitted."""
    if key_file is None:
        return _validate_key(os.environ.get("DEEPSEEK_API_KEY"))
    try:
        lines = Path(key_file).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise ValueError("无法按 UTF-8 读取指定的 DeepSeek 密钥文件。") from None
    if not lines:
        raise ValueError("DeepSeek 密钥文件为空。")
    name, separator, value = lines[0].partition("=")
    if name.strip() != "Deepseek-API-Key" or not separator:
        raise ValueError("DeepSeek 密钥文件首行必须使用 Deepseek-API-Key = value 格式。")
    return _validate_key(value.strip())


class DeepSeekAdapter:
    """Make one bounded provider request and return untrusted action text."""

    def __init__(self, api_key: str, model: str = "deepseek-flash", timeout: float = 30,
                 max_tokens: int = 512):
        self._api_key = _validate_key(api_key)
        if not isinstance(model, str) or not model or any(char.isspace() for char in model):
            raise ValueError("DeepSeek 模型名必须非空且不含空白字符。")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("DeepSeek 超时必须为大于零的有限数值。")
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError("DeepSeek 输出 token 上限必须为正整数。")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._opener = urllib.request.build_opener(_NoRedirect())

    def __repr__(self) -> str:
        return "DeepSeekAdapter(model={!r}, timeout={!r}, max_tokens={!r})".format(self.model, self.timeout, self.max_tokens)

    def generate_decision(self, context: Dict[str, Any]) -> ModelDecision:
        """Send actor context once; parsing and world-rule checks stay in the runtime."""
        try:
            payload = {
                "model": self.model, "messages": build_messages(context),
                "response_format": {"type": "json_object"}, "stream": False,
                "thinking": {"type": "disabled"}, "max_tokens": self.max_tokens,
            }
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (KeyError, TypeError, ValueError):
            raise DeepSeekError("DeepSeek 请求上下文无法编码为有效 JSON。") from None
        request = urllib.request.Request(
            API_URL, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._api_key},
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise DeepSeekError(_http_message(response.status))
                raw_response = response.read()
        except urllib.error.HTTPError as error:
            raise DeepSeekError(_http_message(error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise DeepSeekError("DeepSeek 网络连接失败或请求超时。") from None
        content = self._response_content(raw_response)
        return ModelDecision(content, request={"provider": "deepseek", "prompt_version": PROMPT_VERSION, "body": payload})

    def _response_content(self, raw_response: bytes) -> str:
        if not isinstance(raw_response, bytes):
            raise DeepSeekError("DeepSeek 响应体类型无效。")
        try:
            response = json.loads(raw_response.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise DeepSeekError("DeepSeek 响应不是有效的 UTF-8 JSON。") from None
        if not isinstance(response, dict):
            raise DeepSeekError("DeepSeek 响应必须是 JSON 对象。")
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise DeepSeekError("DeepSeek 响应必须包含一个候选结果。")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise DeepSeekError("DeepSeek 响应被截断，请检查输出 token 上限。")
        if choice.get("finish_reason") != "stop":
            raise DeepSeekError("DeepSeek 响应未正常完成。")
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise DeepSeekError("DeepSeek 响应中的助手消息格式无效。")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise DeepSeekError("DeepSeek 响应内容为空或不是文本。")
        if message.get("tool_calls"):
            raise DeepSeekError("DeepSeek 返回了未支持的工具调用。")
        try:
            decoded_content = json.dumps(json.loads(content, object_pairs_hook=list), ensure_ascii=False)
        except json.JSONDecodeError:
            # Invalid action JSON still belongs to the runtime parser.
            decoded_content = ""
        if self._api_key in content or self._api_key in decoded_content:
            raise DeepSeekError("DeepSeek 响应包含凭据内容，已拒绝记录。")
        return content


def _http_message(status: Any) -> str:
    """Translate only the numeric HTTP status, never a server body or reason."""
    if type(status) is not int:
        return "DeepSeek 请求失败，HTTP 状态无效。"
    categories = {
        400: "请求参数无效", 401: "认证失败，请检查 API Key",
        402: "账户余额不足", 403: "访问被拒绝", 429: "请求受限",
    }
    category = categories.get(status, "重定向已拒绝" if 300 <= status < 400 else "服务暂时不可用" if status >= 500 else "请求失败")
    return "DeepSeek {}（HTTP {}）。".format(category, status)
