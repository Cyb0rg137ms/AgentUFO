"""
ollama_provider.py
==================
Local GGUF / Ollama runtime provider for Agent-IDE Sandbox.
Communicates directly with the Ollama REST API (http://localhost:11434)
allowing execution of local models (e.g. qwen2.5-coder, deepseek-r1, llama3.2, mistral).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from agent_core.llm.base import BaseLLMProvider, LLMResponse


class OllamaProvider(BaseLLMProvider):
    """Local GGUF runner using Ollama daemon."""

    DEFAULT_HOST = "http://127.0.0.1:11434"

    def __init__(
        self,
        model_name: str = "qwen2.5-coder:1.5b",
        host: Optional[str] = None,
        timeout: float = 360.0,
        **kwargs: Any,
    ) -> None:
        if "model" in kwargs and kwargs["model"]:
            model_name = kwargs.pop("model")
        super().__init__(model_name, **kwargs)
        self.host = (host or self.DEFAULT_HOST).rstrip("/")
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "ollama"

    _cached_availability: Optional[Tuple[bool, float]] = None

    def is_available(self, use_cache: bool = False) -> bool:
        """Verifies whether the Ollama server is running locally (fast non-blocking check)."""
        import time

        now = time.time()
        if use_cache and self._cached_availability and (now - self._cached_availability[1]) < 3.0:
            return self._cached_availability[0]

        available = False
        try:
            req = urllib.request.Request(f"{self.host}/api/version", method="GET")
            with urllib.request.urlopen(req, timeout=0.4) as resp:
                available = resp.status == 200
        except Exception:
            available = False

        OllamaProvider._cached_availability = (available, now)
        return available

    def list_local_models(self) -> List[str]:
        """Returns list of all model tags pulled into local Ollama storage."""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m.get("name") for m in data.get("models", [])]
        except Exception:
            return []

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        url = f"{self.host}/api/generate"
        options: Dict[str, Any] = {
            "temperature": temperature,
            "num_predict": max(1, min(int(max_tokens), 8192)),
            "num_ctx": kwargs.get("num_ctx", 131072),
            "num_batch": kwargs.get("num_batch", 512),
            "top_k": kwargs.get("top_k", 40),
            "top_p": kwargs.get("top_p", 0.9),
        }
        if "stop" in kwargs:
            options["stop"] = kwargs["stop"]

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if system_prompt:
            payload["system"] = system_prompt

        try:
            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))

            content = res_json.get("response", "")
            eval_count = res_json.get("eval_count", 0)
            prompt_eval_count = res_json.get("prompt_eval_count", 0)

            return LLMResponse(
                content=content,
                model=self.model_name,
                provider=self.provider_name,
                prompt_tokens=prompt_eval_count,
                completion_tokens=eval_count,
                total_tokens=prompt_eval_count + eval_count,
                raw=res_json,
                finish_reason="stop" if res_json.get("done") else None,
            )
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Failed to connect to local Ollama daemon at {self.host}. "
                f"Ensure Ollama is running (`ollama serve`). Details: {e}"
            )

    def generate_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ):
        """Streams token chunks yielded directly from the local Ollama daemon."""
        url = f"{self.host}/api/generate"
        options: Dict[str, Any] = {
            "temperature": temperature,
            "num_predict": max(1, min(int(max_tokens), 8192)),
            "num_ctx": kwargs.get("num_ctx", 131072),
            "num_batch": kwargs.get("num_batch", 512),
        }
        if "stop" in kwargs:
            options["stop"] = kwargs["stop"]

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": True,
            "options": options,
        }
        if system_prompt:
            payload["system"] = system_prompt

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for line in resp:
                    if line.strip():
                        chunk = json.loads(line.decode("utf-8"))
                        token = chunk.get("response", "")
                        if token:
                            yield token
                        if chunk.get("done"):
                            break
        except Exception as e:
            yield f"\n[Stream Error: {e}]"

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        url = f"{self.host}/api/chat"
        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max(1, min(int(max_tokens), 8192)),
                "num_ctx": kwargs.get("num_ctx", 131072),
            },
        }
        try:
            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))

            msg = res_json.get("message", {})
            content = msg.get("content", "")
            eval_count = res_json.get("eval_count", 0)
            prompt_eval_count = res_json.get("prompt_eval_count", 0)

            return LLMResponse(
                content=content,
                model=self.model_name,
                provider=self.provider_name,
                prompt_tokens=prompt_eval_count,
                completion_tokens=eval_count,
                total_tokens=prompt_eval_count + eval_count,
                raw=res_json,
                finish_reason="stop" if res_json.get("done") else None,
            )
        except urllib.error.URLError as e:
            raise ConnectionError(f"Failed to connect to local Ollama daemon at {self.host}: {e}")
