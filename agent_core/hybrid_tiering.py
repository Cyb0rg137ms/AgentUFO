"""
hybrid_tiering.py
=================
Hybrid Cloud API + Local LLM Collaborative Tiering Architecture ("Save Tokens Feature").
Enables Frontier Cloud LLMs (DeepSeek, OpenAI, Claude, etc.) and Local LLMs (Ollama GGUF)
to work in parallel:
- Frontier Cloud LLM: Handles high-level architectural planning, decomposition, and final convergence.
- Local LLM Capability Inspector: Probes local models and determines capability match.
- Local LLM: Handles parallel leaf tasks, unit test generation, and bug repair, saving 70-90% API tokens.
- Real-time token and dollar cost savings ledger with telemetry.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from agent_core.llm.base import BaseLLMProvider, LLMResponse
from agent_core.llm.ollama_provider import OllamaProvider


@dataclass
class LocalModelCapability:
    """Capability profile inferred for a local model."""

    model_name: str
    family: str
    parameter_billions: float
    quantization: str
    context_window: int
    coding_score: float  # 0.0 to 1.0
    reasoning_score: float  # 0.0 to 1.0
    max_recommended_tokens: int
    can_handle_leaf_code: bool = True
    can_handle_unit_tests: bool = True
    can_handle_syntax_repair: bool = True
    can_handle_architecture_plan: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "family": self.family,
            "parameter_billions": self.parameter_billions,
            "quantization": self.quantization,
            "context_window": self.context_window,
            "coding_score": round(self.coding_score, 2),
            "reasoning_score": round(self.reasoning_score, 2),
            "max_recommended_tokens": self.max_recommended_tokens,
            "can_handle_leaf_code": self.can_handle_leaf_code,
            "can_handle_unit_tests": self.can_handle_unit_tests,
            "can_handle_syntax_repair": self.can_handle_syntax_repair,
            "can_handle_architecture_plan": self.can_handle_architecture_plan,
        }


class LocalCapabilityInspector:
    """
    Probes local inference engines (e.g. Ollama or local OpenAI-compatible server)
    to discover running models, parameter weights, quantization, and context limits.
    """

    KNOWN_FAMILY_BENCHMARKS: Dict[str, Dict[str, float]] = {
        "qwen2.5-coder": {"coding_base": 0.88, "reasoning_base": 0.85},
        "deepseek-coder": {"coding_base": 0.87, "reasoning_base": 0.84},
        "deepseek-r1": {"coding_base": 0.90, "reasoning_base": 0.95},
        "codellama": {"coding_base": 0.78, "reasoning_base": 0.72},
        "llama3": {"coding_base": 0.79, "reasoning_base": 0.80},
        "llama3.2": {"coding_base": 0.77, "reasoning_base": 0.78},
        "starcoder": {"coding_base": 0.80, "reasoning_base": 0.70},
        "mistral": {"coding_base": 0.75, "reasoning_base": 0.78},
        "phi": {"coding_base": 0.80, "reasoning_base": 0.82},
        "gemma": {"coding_base": 0.76, "reasoning_base": 0.77},
    }

    @classmethod
    def probe_ollama(
        cls, host: str = "http://localhost:11434", timeout: float = 1.5
    ) -> List[LocalModelCapability]:
        """Probes the Ollama instance for installed models and extracts capabilities."""
        capabilities: List[LocalModelCapability] = []
        try:
            url = f"{host.rstrip('/')}/api/tags"
            req = urllib.request.Request(url, headers={"User-Agent": "Agent-IDE-Sandbox/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = data.get("models", [])
                    for m in models:
                        name = m.get("name", "")
                        details = m.get("details", {})
                        cap = cls.analyze_model(name, details)
                        capabilities.append(cap)
        except Exception:
            pass
        return capabilities

    @classmethod
    def inspect_model(
        cls, model_name: str, details: Optional[Dict[str, Any]] = None
    ) -> LocalModelCapability:
        """Alias for analyze_model."""
        return cls.analyze_model(model_name, details)

    @classmethod
    def analyze_model(
        cls, model_name: str, details: Optional[Dict[str, Any]] = None
    ) -> LocalModelCapability:
        """Analyzes a model name and metadata to determine capability profile."""
        name_lower = model_name.lower()
        details = details or {}

        # 1. Family detection
        family = details.get("family", "")
        if not family:
            if "qwen" in name_lower:
                family = "qwen"
            elif "deepseek" in name_lower:
                family = "deepseek"
            elif "llama" in name_lower or "codellama" in name_lower:
                family = "llama"
            elif "gemma" in name_lower:
                family = "gemma"
            elif "phi" in name_lower:
                family = "phi"
            elif "mistral" in name_lower:
                family = "mistral"
            else:
                family = "generic-llm"

        # 2. Parameter size detection
        params = 7.0
        param_str = details.get("parameter_size", "")
        if not param_str:
            match = re.search(r"(\d+(?:\.\d+)?)[bB]", model_name)
            if match:
                param_str = match.group(1) + "B"

        if param_str:
            m_val = re.search(r"(\d+(?:\.\d+)?)", param_str)
            if m_val:
                try:
                    params = float(m_val.group(1))
                except Exception:
                    params = 7.0

        # 3. Quantization
        quant = details.get("quantization_level", "")
        if not quant:
            q_match = re.search(r"(q\d+_[a-z0-9_]+|fp16|bf16)", name_lower)
            quant = q_match.group(1) if q_match else "q4_k_m"

        # 4. Context window
        context_window = 32768
        if "128k" in name_lower:
            context_window = 131072
        elif "64k" in name_lower:
            context_window = 65536
        elif "32k" in name_lower:
            context_window = 32768
        elif "8k" in name_lower or "8192" in name_lower:
            context_window = 8192
        elif params <= 3.0:
            context_window = 8192

        bench = {"coding_base": 0.72, "reasoning_base": 0.70}
        for fam_key, b in cls.KNOWN_FAMILY_BENCHMARKS.items():
            if fam_key in name_lower or fam_key == family:
                bench = b
                break
        # Scale score with parameter size logarithmically
        param_factor = min(1.15, max(0.90, 0.90 + (params / 35.0) * 0.20))
        coding_score = min(0.99, bench["coding_base"] * param_factor)
        reasoning_score = min(0.99, bench["reasoning_base"] * param_factor)

        # Thresholds
        can_leaf = coding_score >= 0.70 or params >= 1.0
        can_tests = coding_score >= 0.72 or params >= 1.5
        can_repair = coding_score >= 0.75 or params >= 3.0
        can_plan = reasoning_score >= 0.88 and params >= 14.0

        return LocalModelCapability(
            model_name=model_name,
            family=family,
            parameter_billions=params,
            quantization=quant,
            context_window=context_window,
            coding_score=coding_score,
            reasoning_score=reasoning_score,
            max_recommended_tokens=min(context_window, 8192),
            can_handle_leaf_code=can_leaf,
            can_handle_unit_tests=can_tests,
            can_handle_syntax_repair=can_repair,
            can_handle_architecture_plan=can_plan,
        )


@dataclass
class TokenSavingsLedger:
    """Tracks token economy and monetary savings in hybrid mode."""

    cloud_prompt_tokens: int = 0
    cloud_completion_tokens: int = 0
    local_prompt_tokens: int = 0
    local_completion_tokens: int = 0
    saved_cloud_tokens: int = 0
    tasks_routed_to_local: int = 0
    tasks_routed_to_cloud: int = 0

    # Pricing per 1k tokens for frontier models (blended average $0.003/1k)
    PRICE_PER_1K_CLOUD: float = 0.003

    def record_cloud(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.cloud_prompt_tokens += prompt_tokens
        self.cloud_completion_tokens += completion_tokens
        self.tasks_routed_to_cloud += 1

    def record_local_offload(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.local_prompt_tokens += prompt_tokens
        self.local_completion_tokens += completion_tokens
        self.saved_cloud_tokens += prompt_tokens + completion_tokens
        self.tasks_routed_to_local += 1

    @property
    def total_tokens_processed(self) -> int:
        return (
            self.cloud_prompt_tokens
            + self.cloud_completion_tokens
            + self.local_prompt_tokens
            + self.local_completion_tokens
        )

    @property
    def percentage_saved(self) -> float:
        total = self.total_tokens_processed
        if total == 0:
            return 0.0
        return round((self.saved_cloud_tokens / total) * 100.0, 1)

    @property
    def estimated_usd_saved(self) -> float:
        return round((self.saved_cloud_tokens / 1000.0) * self.PRICE_PER_1K_CLOUD, 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cloud_tokens": self.cloud_prompt_tokens + self.cloud_completion_tokens,
            "local_tokens": self.local_prompt_tokens + self.local_completion_tokens,
            "saved_cloud_tokens": self.saved_cloud_tokens,
            "percentage_saved": self.percentage_saved,
            "estimated_usd_saved": self.estimated_usd_saved,
            "tasks_routed_to_local": self.tasks_routed_to_local,
            "tasks_routed_to_cloud": self.tasks_routed_to_cloud,
        }


class HybridTieringCoordinator:
    """
    Coordinates Frontier Cloud API and Local LLM execution to maximize token savings.
    - Routes architecture planning and multi-perspective verification to Frontier Cloud LLM.
    - Routes leaf task execution, unit assertions, and syntax repair to Local LLM.
    - Falls back safely to Cloud LLM if Local LLM is unavailable or encounters errors.
    """

    def __init__(
        self,
        cloud_llm: BaseLLMProvider,
        local_llm: Optional[BaseLLMProvider] = None,
        enabled: bool = True,
        auto_inspect: bool = True,
    ) -> None:
        self.cloud_llm = cloud_llm
        self.local_llm = local_llm
        self.enabled = enabled
        self.ledger = TokenSavingsLedger()
        self.capability: Optional[LocalModelCapability] = None

        if auto_inspect and not self.local_llm:
            # Auto-detect running local Ollama
            try:
                probe = OllamaProvider()
                if probe.is_available():
                    self.local_llm = probe
            except Exception:
                self.local_llm = None

        if self.local_llm:
            model_name = getattr(self.local_llm, "model_name", "qwen2.5-coder:1.5b")
            self.capability = LocalCapabilityInspector.analyze_model(model_name)

    def is_local_ready(self) -> bool:
        """Returns True if a local model is available and enabled."""
        if not self.enabled or not self.local_llm:
            return False
        try:
            return self.local_llm.is_available()
        except Exception:
            return False

    def can_offload(self, task_type: str) -> bool:
        """Checks if a given subtask type is eligible for local offloading."""
        if not self.is_local_ready():
            return False
        if not self.capability:
            return True

        t = task_type.lower()
        if "leaf" in t or "component" in t or "function" in t:
            return self.capability.can_handle_leaf_code
        if "test" in t or "assertion" in t or "harness" in t:
            return self.capability.can_handle_unit_tests
        if "repair" in t or "fix" in t or "syntax" in t:
            return self.capability.can_handle_syntax_repair
        if "plan" in t or "architecture" in t or "blueprint" in t or "consensus" in t:
            return self.capability.can_handle_architecture_plan
        return self.capability.coding_score >= 0.75

    def generate(
        self,
        prompt: str,
        task_type: str = "leaf_code",
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> Tuple[LLMResponse, str]:
        """
        Executes generation on either Local LLM (if eligible) or Cloud LLM.
        Returns (LLMResponse, tier_used: 'local' | 'cloud').
        """
        # Decide destination
        use_local = self.can_offload(task_type)
        if use_local and self.local_llm:
            try:
                resp = self.local_llm.generate(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=min(
                        max_tokens, getattr(self.capability, "max_recommended_tokens", 4096)
                    ),
                    **kwargs,
                )
                if resp and resp.content and resp.content.strip():
                    p_tok = resp.prompt_tokens or max(1, len(prompt) // 4)
                    c_tok = resp.completion_tokens or max(1, len(resp.content) // 4)
                    self.ledger.record_local_offload(p_tok, c_tok)
                    return resp, "local"
            except Exception:
                # Fallback to cloud on any local failure
                pass

        # Execute on Frontier Cloud LLM
        resp = self.cloud_llm.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        p_tok = resp.prompt_tokens or max(1, len(prompt) // 4)
        c_tok = resp.completion_tokens or max(1, len(resp.content) // 4)
        self.ledger.record_cloud(p_tok, c_tok)
        return resp, "cloud"

    generate_tiered = generate

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive status and telemetry for the UI/API."""
        return {
            "enabled": self.enabled,
            "local_ready": self.is_local_ready(),
            "cloud_provider": getattr(self.cloud_llm, "provider_name", "none"),
            "cloud_model": getattr(self.cloud_llm, "model_name", "none"),
            "local_provider": getattr(self.local_llm, "provider_name", "none")
            if self.local_llm
            else "none",
            "local_model": getattr(self.local_llm, "model_name", "none")
            if self.local_llm
            else "none",
            "capability": self.capability.to_dict() if self.capability else None,
            "ledger": self.ledger.to_dict(),
        }
