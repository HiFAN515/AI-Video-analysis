"""OpenAI-compatible LLM client (DeepSeek, OpenAI, Qwen, Ollama, ...)."""
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import settings
from .settings_store import RuntimeSettings


class LLMError(RuntimeError):
    pass


@dataclass
class LLMDelta:
    """A streaming chunk. Thinking models emit reasoning before content."""

    kind: str  # "reasoning" | "content"
    text: str


def _endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("尚未配置大模型接口地址")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def is_local_url(url: str) -> bool:
    lowered = (url or "").lower()
    return any(host in lowered for host in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))


class LLMClient:
    def __init__(self, runtime: RuntimeSettings) -> None:
        self.runtime = runtime

    @property
    def model(self) -> str:
        return (self.runtime.llm_model or "").strip()

    def configured(self) -> bool:
        if not self.model or not self.runtime.llm_base_url:
            return False
        return bool(self.runtime.llm_api_key) or is_local_url(self.runtime.llm_base_url)

    def _proxy(self) -> str | None:
        if is_local_url(self.runtime.llm_base_url):
            return None
        return settings.proxy or None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.runtime.llm_api_key:
            headers["Authorization"] = f"Bearer {self.runtime.llm_api_key}"
        return headers

    def stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[LLMDelta]:
        if not self.configured():
            raise LLMError("尚未配置大模型 API Key，请前往「设置」填写")
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": self.runtime.llm_temperature if temperature is None else temperature,
            "stream": True,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        timeout = httpx.Timeout(connect=20.0, read=600.0, write=60.0, pool=30.0)
        try:
            with httpx.stream(
                "POST",
                _endpoint(self.runtime.llm_base_url),
                headers=self._headers(),
                json=payload,
                timeout=timeout,
                proxy=self._proxy(),
                trust_env=False,
            ) as response:
                if response.status_code >= 400:
                    detail = _read_error(response)
                    raise LLMError(_status_message(response.status_code, detail))
                for line in response.iter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data in {"[DONE]", ""}:
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    reasoning = delta.get("reasoning_content") or ""
                    if reasoning:
                        yield LLMDelta("reasoning", reasoning)
                    piece = delta.get("content") or ""
                    if piece:
                        yield LLMDelta("content", piece)
        except httpx.HTTPError as exc:
            raise LLMError(f"连接大模型服务失败：{exc}") from exc

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if not self.configured():
            raise LLMError("尚未配置大模型 API Key，请前往「设置」填写")
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": self.runtime.llm_temperature if temperature is None else temperature,
            "stream": False,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        timeout = httpx.Timeout(connect=20.0, read=600.0, write=60.0, pool=30.0)
        try:
            response = httpx.post(
                _endpoint(self.runtime.llm_base_url),
                headers=self._headers(),
                json=payload,
                timeout=timeout,
                proxy=self._proxy(),
                trust_env=False,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"连接大模型服务失败：{exc}") from exc
        if response.status_code >= 400:
            raise LLMError(_status_message(response.status_code, _read_error(response)))
        try:
            data = response.json()
            return str(data["choices"][0]["message"].get("content") or "")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("大模型返回了无法解析的内容") from exc

    def test(self, *, base_url: str | None = None, model: str | None = None, api_key: str | None = None) -> tuple[bool, str, int]:
        original = (self.runtime.llm_base_url, self.runtime.llm_model, self.runtime.llm_api_key)
        if base_url:
            self.runtime.llm_base_url = base_url.rstrip("/")
        if model:
            self.runtime.llm_model = model
        if api_key:
            self.runtime.llm_api_key = api_key
        started = time.time()
        try:
            text = self.complete(
                [
                    {"role": "system", "content": "你是一个连通性测试助手。"},
                    {"role": "user", "content": "请只回复两个字：正常"},
                ],
                max_tokens=16,
                temperature=0.0,
            )
            elapsed = int((time.time() - started) * 1000)
            return True, "连接成功", elapsed
        except LLMError as exc:
            return False, str(exc), None
        finally:
            self.runtime.llm_base_url, self.runtime.llm_model, self.runtime.llm_api_key = original


def _read_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
        return str(payload.get("message") or "")[:500]
    except Exception:
        return (response.text or "")[:500]


def _status_message(status: int, detail: str) -> str:
    mapping = {
        400: "请求参数有误",
        401: "API Key 无效或已过期",
        403: "没有访问该模型的权限",
        404: "模型或接口地址不存在，请检查 Base URL 和模型名",
        429: "请求过于频繁或额度不足，请稍后重试",
        500: "大模型服务内部错误",
        502: "大模型网关错误",
        503: "大模型服务暂时不可用",
    }
    base = mapping.get(status, f"大模型请求失败（HTTP {status}）")
    return f"{base}：{detail}" if detail else base
