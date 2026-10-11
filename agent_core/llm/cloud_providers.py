"""
cloud_providers.py
==================
Frontier Cloud API providers for Agent-IDE Sandbox.
Supports:
  - OpenAI (GPT-4o, o1, o3-mini, etc.)
  - Anthropic Claude (Claude 3.7 Sonnet, Claude 3.5 Sonnet, Claude 3 Opus)
  - xAI Grok (grok-2, grok-beta)
  - Google Gemini (Gemini 1.5/2.0 Flash/Pro)
  - OpenAI-compatible local endpoints (llama.cpp, LM Studio, vLLM)
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
from agent_core.llm.base import BaseLLMProvider, LLMResponse


class OpenAICompatibleProvider(BaseLLMProvider):
    """Generic provider for OpenAI API and any OpenAI-compatible endpoint."""

    DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def __init__(
        self,
        model_name: str = "gpt-4o",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 60.0,
        provider_name_override: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model_name, **kwargs)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        self.timeout = timeout
        self._provider_name = provider_name_override or "openai"

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def is_available(self) -> bool:
        # If running against local endpoint (e.g. localhost), key might not be required
        if "localhost" in self.base_url or "127.0.0.1" in self.base_url:
            return True
        return bool(self.api_key)

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, temperature=temperature, max_tokens=max_tokens, **kwargs)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None and max_tokens > 0:
            # Local OpenAI-compatible servers also need a hard cap; local != unlimited.
            payload["max_tokens"] = min(int(max_tokens), 16_384)
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                res = json.loads(resp.read().decode("utf-8"))

            choice = res["choices"][0]
            content = choice["message"].get("content", "")
            usage = res.get("usage", {})

            return LLMResponse(
                content=content,
                model=res.get("model", self.model_name),
                provider=self.provider_name,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                raw=res,
                finish_reason=choice.get("finish_reason"),
            )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else str(e)
            raise RuntimeError(f"{self.provider_name} API HTTP error {e.code}: {err_body}")
        except Exception as e:
            raise RuntimeError(f"Error communicating with {self.provider_name} at {url}: {e}")


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude Messages API provider."""

    DEFAULT_BASE_URL = "https://api.anthropic.com/v1"

    def __init__(
        self,
        model_name: str = "claude-3-7-sonnet-20250219",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        anthropic_version: str = "2023-06-01",
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(model_name, **kwargs)
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.anthropic_version = anthropic_version
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        messages = [{"role": "user", "content": prompt}]
        return self._call_messages(messages, system_prompt, temperature, max_tokens)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        system = ""
        claude_msgs = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                claude_msgs.append({"role": m["role"], "content": m["content"]})
        return self._call_messages(claude_msgs, system or None, temperature, max_tokens)

    def _call_messages(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        url = f"{self.base_url}/messages"
        safe_max = min(max_tokens, 8192) if max_tokens > 0 else 8192
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": safe_max,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                res = json.loads(resp.read().decode("utf-8"))

            content = "".join(
                b.get("text", "") for b in res.get("content", []) if b.get("type") == "text"
            )
            usage = res.get("usage", {})
            prompt_tokens = usage.get("input_tokens", 0)
            completion_tokens = usage.get("output_tokens", 0)

            return LLMResponse(
                content=content,
                model=res.get("model", self.model_name),
                provider=self.provider_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                raw=res,
                finish_reason=res.get("stop_reason"),
            )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else str(e)
            raise RuntimeError(f"Anthropic API error {e.code}: {err_body}")


class GrokProvider(OpenAICompatibleProvider):
    """xAI Grok provider via standard API format."""

    def __init__(
        self,
        model_name: str = "grok-2-latest",
        api_key: Optional[str] = None,
        base_url: str = "https://api.x.ai/v1",
        **kwargs: Any,
    ) -> None:
        key = api_key or os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY", "")
        super().__init__(
            model_name=model_name,
            api_key=key,
            base_url=base_url,
            provider_name_override="grok",
            **kwargs,
        )


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek API provider (DeepSeek-V3, DeepSeek-R1 / Reasoner, Coder)."""

    DEFAULT_BASE_URL = "https://api.deepseek.com"

    def __init__(
        self,
        model_name: str = "deepseek-chat",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        super().__init__(
            model_name=model_name,
            api_key=key,
            base_url=base_url or self.DEFAULT_BASE_URL,
            provider_name_override="deepseek",
            **kwargs,
        )


class KimiProvider(OpenAICompatibleProvider):
    """Moonshot AI Kimi provider (Kimi-k2, Moonshot-v1-8k/32k/128k)."""

    DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"

    def __init__(
        self,
        model_name: str = "kimi-k2",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        key = api_key or os.getenv("MOONSHOT_API_KEY") or os.getenv("KIMI_API_KEY", "")
        super().__init__(
            model_name=model_name,
            api_key=key,
            base_url=base_url or self.DEFAULT_BASE_URL,
            provider_name_override="kimi",
            **kwargs,
        )


class MiniMaxProvider(OpenAICompatibleProvider):
    """MiniMax API provider (MiniMax-Text-01, abab6.5s, abab6.5)."""

    DEFAULT_BASE_URL = "https://api.minimax.chat/v1"

    def __init__(
        self,
        model_name: str = "MiniMax-Text-01",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        key = api_key or os.getenv("MINIMAX_API_KEY", "")
        super().__init__(
            model_name=model_name,
            api_key=key,
            base_url=base_url or self.DEFAULT_BASE_URL,
            provider_name_override="minimax",
            **kwargs,
        )


class QwenProvider(OpenAICompatibleProvider):
    """Alibaba Cloud DashScope Qwen provider (qwen-max, qwen-plus, qwen-2.5-coder)."""

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(
        self,
        model_name: str = "qwen-max",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        key = api_key or os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY", "")
        super().__init__(
            model_name=model_name,
            api_key=key,
            base_url=base_url or self.DEFAULT_BASE_URL,
            provider_name_override="qwen",
            **kwargs,
        )


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API provider with automatic backoff retry and smart model fallback."""

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    FALLBACK_MODELS = [
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-3.1-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
    ]

    def __init__(
        self,
        model_name: str = "gemini-3.5-flash-lite",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 8.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(model_name, **kwargs)
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not self.api_key:
            try:
                env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
                if os.path.exists(env_path):
                    with open(env_path, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.strip().startswith("GEMINI_API_KEY="):
                                self.api_key = line.strip().split("GEMINI_API_KEY=", 1)[1].strip()
                                os.environ["GEMINI_API_KEY"] = self.api_key
                                break
            except Exception:
                pass
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "gemini"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": f"System Instructions: {system_prompt}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        safe_max_tokens = min(max_tokens, 16_384) if max_tokens and max_tokens > 0 else 4096
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": safe_max_tokens,
            },
        }

        # Build ordered list of candidate models: target model first, then fallback models
        models_to_try = [self.model_name]
        for m in self.FALLBACK_MODELS:
            if m not in models_to_try:
                models_to_try.append(m)

        last_error = None
        for model_candidate in models_to_try:
            url = f"{self.base_url}/models/{model_candidate}:generateContent?key={self.api_key}"
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    res = json.loads(resp.read().decode("utf-8"))

                candidates = res.get("candidates", [])
                content = ""
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    content = "".join(p.get("text", "") for p in parts)

                return LLMResponse(
                    content=content,
                    model=model_candidate,
                    provider=self.provider_name,
                    raw=res,
                    finish_reason=candidates[0].get("finishReason") if candidates else None,
                )
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8") if e.fp else str(e)
                last_error = f"Gemini API error {e.code}: {err_body}"

                # If 503 (high demand), 404 (deprecated), or 429 (quota), fall through to next active candidate
                if e.code in (503, 404, 429):
                    continue
                else:
                    raise RuntimeError(last_error)
            except Exception as ex:
                last_error = str(ex)
                continue

        raise RuntimeError(last_error or f"Gemini API failed across models: {models_to_try}")

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        system = ""
        user_parts = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                user_parts.append(f"{m.get('role').upper()}: {m.get('content')}")
        full_prompt = "\n\n".join(user_parts)
        return self.generate(full_prompt, system_prompt=system or None, temperature=temperature, max_tokens=max_tokens, **kwargs)



# -----------------------------------------------------------------------
# DeepSeek AI  (OpenAI-compatible)
# -----------------------------------------------------------------------

class DeepSeekProvider(OpenAICompatibleProvider):
    DEFAULT_BASE_URL = 'https://api.deepseek.com/v1'
    def __init__(self, model_name='deepseek-chat', api_key=None, base_url=None, timeout=60.0, **kwargs):
        super().__init__(model_name=model_name,
            api_key=api_key or os.getenv('DEEPSEEK_API_KEY', ''),
            base_url=base_url or os.getenv('DEEPSEEK_BASE_URL', self.DEFAULT_BASE_URL),
            timeout=timeout, provider_name_override='deepseek', **kwargs)
    def is_available(self): return bool(self.api_key)


# -----------------------------------------------------------------------
# Moonshot AI / Kimi  (OpenAI-compatible)
# -----------------------------------------------------------------------

class KimiProvider(OpenAICompatibleProvider):
    DEFAULT_BASE_URL = 'https://api.moonshot.cn/v1'
    def __init__(self, model_name='kimi-k2', api_key=None, base_url=None, timeout=60.0, **kwargs):
        super().__init__(model_name=model_name,
            api_key=api_key or os.getenv('MOONSHOT_API_KEY', '') or os.getenv('KIMI_API_KEY', ''),
            base_url=base_url or os.getenv('MOONSHOT_BASE_URL', self.DEFAULT_BASE_URL),
            timeout=timeout, provider_name_override='kimi', **kwargs)
    def is_available(self): return bool(self.api_key)


# -----------------------------------------------------------------------
# MiniMax AI  (OpenAI-compatible)
# -----------------------------------------------------------------------

class MiniMaxProvider(OpenAICompatibleProvider):
    DEFAULT_BASE_URL = 'https://api.minimax.chat/v1'
    def __init__(self, model_name='MiniMax-Text-01', api_key=None, base_url=None, timeout=60.0, **kwargs):
        super().__init__(model_name=model_name,
            api_key=api_key or os.getenv('MINIMAX_API_KEY', ''),
            base_url=base_url or os.getenv('MINIMAX_BASE_URL', self.DEFAULT_BASE_URL),
            timeout=timeout, provider_name_override='minimax', **kwargs)
    def is_available(self): return bool(self.api_key)


# -----------------------------------------------------------------------
# Alibaba Qwen / DashScope  (OpenAI-compatible)
# -----------------------------------------------------------------------

class QwenProvider(OpenAICompatibleProvider):
    DEFAULT_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    def __init__(self, model_name='qwen-max', api_key=None, base_url=None, timeout=60.0, **kwargs):
        super().__init__(model_name=model_name,
            api_key=api_key or os.getenv('DASHSCOPE_API_KEY', '') or os.getenv('QWEN_API_KEY', ''),
            base_url=base_url or os.getenv('DASHSCOPE_BASE_URL', self.DEFAULT_BASE_URL),
            timeout=timeout, provider_name_override='qwen', **kwargs)
    def is_available(self): return bool(self.api_key)
