from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_core.llm.base import BaseLLMProvider, UnavailableLLMProvider
from agent_core.llm.ollama_provider import OllamaProvider

try:
    from agent_core.llm.cloud_providers import (
        OpenAICompatibleProvider,
        AnthropicProvider,
        GrokProvider,
        GeminiProvider,
        DeepSeekProvider,
        KimiProvider,
        MiniMaxProvider,
        QwenProvider,
    )
    _CLOUD_AVAILABLE = True
except ImportError:
    _CLOUD_AVAILABLE = False
    class _MissingProvider(BaseLLMProvider):
        @property
        def provider_name(self): return "missing"
        def generate(self, *a, **kw): raise ImportError("Cloud providers not available")
        def chat(self, *a, **kw): raise ImportError("Cloud providers not available")
        def is_available(self): return False
    OpenAICompatibleProvider = AnthropicProvider = GrokProvider = GeminiProvider = _MissingProvider
    DeepSeekProvider = KimiProvider = MiniMaxProvider = QwenProvider = _MissingProvider


class LLMRegistry:
    _PROVIDERS: Dict[str, Any] = {
        "ollama": OllamaProvider,
        "unconfigured": UnavailableLLMProvider,
    }
    if _CLOUD_AVAILABLE:
        _PROVIDERS.update({
            "openai": OpenAICompatibleProvider,
            "anthropic": AnthropicProvider,
            "claude": AnthropicProvider,
            "grok": GrokProvider,
            "gemini": GeminiProvider,
            "deepseek": DeepSeekProvider,
            "kimi": KimiProvider,
            "moonshot": KimiProvider,
            "minimax": MiniMaxProvider,
            "qwen": QwenProvider,
            "dashscope": QwenProvider,
            "local": OpenAICompatibleProvider,
        })

    DEFAULT_MODELS: Dict[str, str] = {
        "ollama": "qwen2.5-coder:1.5b",
        "openai": "gpt-4o",
        "anthropic": "claude-3-7-sonnet-20250219",
        "claude": "claude-3-7-sonnet-20250219",
        "grok": "grok-2-latest",
        "gemini": "gemini-2.0-flash",
        "deepseek": "deepseek-chat",
        "kimi": "kimi-k2",
        "moonshot": "kimi-k2",
        "minimax": "MiniMax-Text-01",
        "qwen": "qwen-max",
        "dashscope": "qwen-max",
        "local": "local-model",
    }

    # Ordered provider specs for generic heuristic + online probe detection
    PROVIDER_SPECS: List[Dict[str, Any]] = [
        {
            "provider": "deepseek", "name": "DeepSeek AI",
            "env_var": "DEEPSEEK_API_KEY", "default_model": "deepseek-chat",
            "probe_url": "https://api.deepseek.com/models",
            "probe_type": "bearer",
            "pattern": r"^sk-[0-9a-fA-F]{32}$",
            "fallback_models": ["deepseek-chat", "deepseek-reasoner", "deepseek-flash", "deepseek-v4-pro"],
        },
        {
            "provider": "anthropic", "name": "Anthropic Claude",
            "env_var": "ANTHROPIC_API_KEY", "default_model": "claude-3-7-sonnet-20250219",
            "probe_url": "https://api.anthropic.com/v1/models",
            "probe_type": "anthropic",
            "pattern": r"^sk-ant-[a-zA-Z0-9_\-]{20,}$",
            "fallback_models": ["claude-3-7-sonnet-20250219", "claude-3-5-sonnet-20241022", "claude-3-opus-20240229"],
        },
        {
            "provider": "gemini", "name": "Google Gemini",
            "env_var": "GEMINI_API_KEY", "default_model": "gemini-2.0-flash",
            "probe_url": "https://generativelanguage.googleapis.com/v1beta/models?key={KEY}",
            "probe_type": "gemini_query",
            "pattern": r"^(AIza[0-9A-Za-z\-_]{35}|AQ\.[a-zA-Z0-9_\-]{20,})$",
            "fallback_models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
        },
        {
            "provider": "grok", "name": "xAI Grok",
            "env_var": "XAI_API_KEY", "default_model": "grok-2-latest",
            "probe_url": "https://api.x.ai/v1/models",
            "probe_type": "bearer",
            "pattern": r"^xai-[a-zA-Z0-9_\-]{20,}$",
            "fallback_models": ["grok-2-latest", "grok-beta"],
        },
        {
            "provider": "kimi", "name": "Moonshot AI Kimi",
            "env_var": "MOONSHOT_API_KEY", "default_model": "kimi-k2",
            "probe_url": "https://api.moonshot.cn/v1/models",
            "probe_type": "bearer",
            "pattern": r"^sk-[a-zA-Z0-9_\-]{30,}$",
            "fallback_models": ["kimi-k2", "moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
        },
        {
            "provider": "minimax", "name": "MiniMax",
            "env_var": "MINIMAX_API_KEY", "default_model": "MiniMax-Text-01",
            "probe_url": "https://api.minimax.chat/v1/models",
            "probe_type": "bearer",
            "pattern": r"^(eyJ[a-zA-Z0-9_\-\.]+|sk-[a-zA-Z0-9_\-]{30,})$",
            "fallback_models": ["MiniMax-Text-01", "abab6.5s-chat", "abab6.5-chat"],
        },
        {
            "provider": "qwen", "name": "Alibaba Cloud Qwen",
            "env_var": "DASHSCOPE_API_KEY", "default_model": "qwen-max",
            "probe_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
            "probe_type": "bearer",
            "pattern": r"^sk-[a-zA-Z0-9_\-]{30,}$",
            "fallback_models": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen2.5-coder-32b-instruct"],
        },
        {
            "provider": "openai", "name": "OpenAI",
            "env_var": "OPENAI_API_KEY", "default_model": "gpt-4o",
            "probe_url": "https://api.openai.com/v1/models",
            "probe_type": "bearer",
            "pattern": r"^sk-(proj-)?[a-zA-Z0-9_\-]{20,}$",
            "fallback_models": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
        },
    ]

    @classmethod
    def get_provider(cls, provider_name: Optional[str] = None, model_name: Optional[str] = None,
                     config_path: Optional[str] = None, **kwargs: Any) -> BaseLLMProvider:
        if not provider_name or provider_name.strip().lower() in ("auto", "default"):
            return cls.auto_detect(config_path)

        p_name = provider_name.lower().strip()

        provider_cls = cls._PROVIDERS.get(p_name)
        if not provider_cls:
            raise ValueError(
                "Unknown provider: {}. Available: {}".format(provider_name, list(cls._PROVIDERS.keys()))
            )
        merged_kwargs: Dict[str, Any] = dict(kwargs)
        if config_path and Path(config_path).exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    file_conf = json.load(f)
                    p_conf = file_conf.get("providers", {}).get(p_name, {})
                    for k, v in p_conf.items():
                        if k not in merged_kwargs:
                            merged_kwargs[k] = v
            except Exception:
                pass
        model = model_name or merged_kwargs.pop("model", None) or cls.DEFAULT_MODELS.get(p_name, "default-model")
        return provider_cls(model_name=model, **merged_kwargs)

    @classmethod
    def auto_detect(cls, config_path: Optional[str] = None) -> BaseLLMProvider:
        """Precedence: Ollama (running) > DeepSeek > OpenAI > Claude > Grok > Gemini > Kimi > MiniMax > Qwen."""
        try:
            ollama = OllamaProvider()
            if ollama.is_available():
                models = ollama.list_local_models()
                chosen_model = models[0] if models else "qwen2.5-coder:1.5b"
                return OllamaProvider(model_name=chosen_model)
        except Exception:
            pass

        if _CLOUD_AVAILABLE:
            if os.getenv("DEEPSEEK_API_KEY"):
                try:
                    return DeepSeekProvider()
                except Exception:
                    pass
            if os.getenv("OPENAI_API_KEY"):
                try:
                    return OpenAICompatibleProvider()
                except Exception:
                    pass
            if os.getenv("ANTHROPIC_API_KEY"):
                try:
                    return AnthropicProvider()
                except Exception:
                    pass
            if os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY"):
                try:
                    return GrokProvider()
                except Exception:
                    pass
            if os.getenv("GEMINI_API_KEY"):
                try:
                    return GeminiProvider()
                except Exception:
                    pass
            if os.getenv("MOONSHOT_API_KEY") or os.getenv("KIMI_API_KEY"):
                try:
                    return KimiProvider()
                except Exception:
                    pass
            if os.getenv("MINIMAX_API_KEY"):
                try:
                    return MiniMaxProvider()
                except Exception:
                    pass
            if os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY"):
                try:
                    return QwenProvider()
                except Exception:
                    pass

        # The product must boot even before the user configures a provider.
        # This provider fails closed on generation and never fabricates output.
        return UnavailableLLMProvider(
            "No active LLM provider found (no reachable Ollama runtime or configured API key)"
        )

    @classmethod
    def generic_detect_provider_and_model(
        cls, raw_key: str, probe_online: bool = True
    ) -> Dict[str, Any]:
        """
        Generic API key detection. Tries:
        1. Unambiguous regex patterns (high-confidence prefix matching)
        2. Online HTTP probe per matching provider (if probe_online=True)
        3. Best-match heuristic fallback
        """
        key = (raw_key or "").strip()
        if not key:
            return {
                "detected": False, "provider": None, "model": None,
                "models": [], "verified": False, "message": "Empty key.",
            }

        # Local / Ollama host shortcut
        if "11434" in key or "localhost" in key or key.lower() == "ollama":
            return {
                "detected": True, "provider": "ollama", "model": "qwen2.5-coder:7b",
                "models": ["qwen2.5-coder:7b", "deepseek-r1:14b", "llama3.2:3b"],
                "verified": True, "env_var": "OLLAMA_HOST", "provider_name": "Ollama Local",
                "message": "Auto-detected local Ollama instance.",
            }

        # Score all providers
        scored: List[Any] = []
        for spec in cls.PROVIDER_SPECS:
            pat = spec.get("pattern", "")
            base_score = 10 if (pat and re.match(pat, key)) else 0
            p = spec["provider"]
            # Override with unambiguous boosts
            if p == "deepseek" and re.match(r"^sk-[0-9a-fA-F]{32}$", key):
                base_score = 100
            elif p == "anthropic" and key.startswith("sk-ant-"):
                base_score = 100
            elif p == "grok" and key.startswith("xai-"):
                base_score = 100
            elif p == "gemini" and (key.startswith("AIza") or key.startswith("AQ.")):
                base_score = 100
            elif p == "openai" and key.startswith("sk-proj-"):
                base_score = 90
            scored.append((base_score, spec))
        scored.sort(key=lambda x: x[0], reverse=True)

        if probe_online:
            for score, spec in scored:
                # Skip zero-score specs if any spec has a positive match
                if score <= 0 and scored[0][0] > 0:
                    continue
                probe_url = spec["probe_url"].replace("{KEY}", key)
                headers: Dict[str, str] = {"User-Agent": "Agent-IDE-Sandbox/2.0"}
                if spec["probe_type"] == "bearer":
                    headers["Authorization"] = "Bearer " + key
                elif spec["probe_type"] == "anthropic":
                    headers["x-api-key"] = key
                    headers["anthropic-version"] = "2023-06-01"
                try:
                    req = urllib.request.Request(probe_url, headers=headers, method="GET")
                    with urllib.request.urlopen(req, timeout=2.5) as resp:
                        if resp.status == 200:
                            raw = json.loads(resp.read().decode("utf-8"))
                            found_models: List[str] = []
                            if isinstance(raw.get("data"), list):
                                found_models = [
                                    m["id"] for m in raw["data"]
                                    if isinstance(m, dict) and "id" in m
                                ]
                            elif isinstance(raw.get("models"), list):
                                for m in raw["models"]:
                                    nm = m.get("name") or m.get("id", "")
                                    if nm:
                                        found_models.append(nm.split("/")[-1])

                            pref_map: Dict[str, List[str]] = {
                                "deepseek": ["deepseek-chat", "deepseek-flash", "deepseek-v4-pro", "deepseek-reasoner"],
                                "kimi": ["kimi-k2", "moonshot-v1-128k", "moonshot-v1-32k"],
                                "minimax": ["MiniMax-Text-01", "abab6.5s-chat"],
                                "qwen": ["qwen-max", "qwen-plus", "qwen2.5-coder-32b-instruct"],
                            }
                            selected = spec["default_model"]
                            if found_models:
                                prefs = pref_map.get(spec["provider"], [spec["default_model"]])
                                for pref in prefs:
                                    if pref in found_models:
                                        selected = pref
                                        break
                                else:
                                    if spec["default_model"] in found_models:
                                        selected = spec["default_model"]
                                    else:
                                        selected = found_models[0]

                            msg = "Online verification with {} succeeded! Model: {}.".format(
                                spec["name"], selected
                            )
                            return {
                                "detected": True, "verified": True,
                                "provider": spec["provider"], "provider_name": spec["name"],
                                "model": selected,
                                "models": found_models or spec["fallback_models"],
                                "env_var": spec["env_var"],
                                "message": msg,
                            }
                except Exception:
                    continue

        # Offline heuristic fallback
        if scored and scored[0][0] > 0:
            spec = scored[0][1]
            return {
                "detected": True, "verified": False,
                "provider": spec["provider"], "provider_name": spec["name"],
                "model": spec["default_model"],
                "models": spec["fallback_models"],
                "env_var": spec["env_var"],
                "message": "Pattern matched {} (offline heuristic).".format(spec["name"]),
            }
        return {
            "detected": False, "provider": None, "model": None, "models": [],
            "verified": False,
            "message": "Key format not recognized across all supported providers.",
        }
