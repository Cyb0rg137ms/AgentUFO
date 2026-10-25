"""
base.py
=======
Base protocol and data contracts for LLM providers in Agent-IDE Sandbox.
Supports both local GGUF/Ollama runtimes and cloud frontier APIs (OpenAI, Claude, Grok, Gemini).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""

    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)
    finish_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "provider": self.provider,
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "total": self.total_tokens,
            },
            "finish_reason": self.finish_reason,
        }


class BaseLLMProvider(ABC):
    """Abstract interface for local and API LLM execution."""

    def __init__(self, model_name: str = "default-model", **kwargs: Any) -> None:
        self.model_name = model_name
        self.config = kwargs

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Returns the canonical name of the provider."""
        raise NotImplementedError

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generates a text completion for a prompt."""
        raise NotImplementedError

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        """Executes a multi-turn chat completion."""
        raise NotImplementedError

    def is_available(self) -> bool:
        """Checks if the provider endpoint is reachable / configured."""
        return True

    def extract_code_block(self, text: str, language: str = "python") -> str:
        """Utility to extract fenced code blocks from markdown responses."""
        if not text:
            return ""

        marker = f"```{language}"
        if marker in text:
            parts = text.split(marker, 1)[1]
            if "```" in parts:
                return parts.split("```", 1)[0].strip()
            return parts.strip()

        if "```" in text:
            parts = text.split("```", 1)[1]
            if "```" in parts:
                return parts.split("```", 1)[0].strip()
            return parts.strip()

        return text.strip()


class UnavailableLLMProvider(BaseLLMProvider):
    """Explicit no-provider state used to let the UI boot before setup.

    Requests fail with an actionable error when no real provider is configured
    instead of returning fabricated assistant output.
    """

    def __init__(
        self,
        model_name: str = "not-configured",
        reason: str = "No LLM provider is configured",
        **kwargs: Any,
    ) -> None:
        super().__init__(model_name=model_name, **kwargs)
        self.reason = reason

    @property
    def provider_name(self) -> str:
        return "unconfigured"

    def is_available(self) -> bool:
        return False

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        raise RuntimeError(
            f"{self.reason}. Start Ollama or configure a provider API key in Settings / .env."
        )

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        raise RuntimeError(
            f"{self.reason}. Start Ollama or configure a provider API key in Settings / .env."
        )
