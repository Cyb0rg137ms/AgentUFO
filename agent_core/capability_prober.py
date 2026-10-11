"""
capability_prober.py
====================
Empirical Machine Capability Prober with Hardware/Model Benchmarking & Veto Authority.

Tests installed models on the actual machine:
1. Exact context length from Ollama /api/show.
2. Fenced file emission test (verifies valid Markdown block output).
3. Micro coding fixture test (runs test assertion in execution sandbox).
4. Veto authority: Vetoes weak/broken models suggested by planner.
5. Persistent disk cache in .agentufo/probe_cache.json.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_core.sandbox import CodeExecutionSandbox


@dataclass
class EmpiricalModelProfile:
    """Empirically verified profile of a model installed on the host."""
    model_name: str
    context_length: int
    parameter_size: str
    quantization_level: str
    fenced_emission_ok: bool
    coding_fixture_passed: bool
    probed_at: float
    vetoed: bool
    veto_reason: Optional[str] = None
    response_latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "context_length": self.context_length,
            "parameter_size": self.parameter_size,
            "quantization_level": self.quantization_level,
            "fenced_emission_ok": self.fenced_emission_ok,
            "coding_fixture_passed": self.coding_fixture_passed,
            "probed_at": self.probed_at,
            "vetoed": self.vetoed,
            "veto_reason": self.veto_reason,
            "response_latency_ms": round(self.response_latency_ms, 1),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> EmpiricalModelProfile:
        return cls(
            model_name=d["model_name"],
            context_length=d.get("context_length", 4096),
            parameter_size=d.get("parameter_size", "unknown"),
            quantization_level=d.get("quantization_level", "unknown"),
            fenced_emission_ok=d.get("fenced_emission_ok", True),
            coding_fixture_passed=d.get("coding_fixture_passed", True),
            probed_at=d.get("probed_at", 0.0),
            vetoed=d.get("vetoed", False),
            veto_reason=d.get("veto_reason"),
            response_latency_ms=d.get("response_latency_ms", 0.0),
        )


class EmpiricalCapabilityProber:
    """
    Probes installed local models by running live execution fixtures,
    caching verification results to disk, and exercising veto authority.
    """

    def __init__(self, workspace_root: Optional[Path] = None, host: str = "http://localhost:11434") -> None:
        self.workspace_root = workspace_root or Path.cwd()
        self.host = host.rstrip("/")
        self.cache_dir = self.workspace_root / ".agentufo"
        self.cache_file = self.cache_dir / "probe_cache.json"
        self.sandbox = CodeExecutionSandbox(timeout_seconds=4.0)
        self._profiles: Dict[str, EmpiricalModelProfile] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    self._profiles[k] = EmpiricalModelProfile.from_dict(v)
            except Exception:
                self._profiles = {}

    def _persist_cache(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in self._profiles.items()}
        temp_file = self.cache_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(str(temp_file), str(self.cache_file))

    def get_profile(self, model_name: str) -> Optional[EmpiricalModelProfile]:
        """Returns cached profile if already probed."""
        return self._profiles.get(model_name)

    def is_vetoed(self, model_name: str) -> Tuple[bool, Optional[str]]:
        """
        Veto Gate: Checks if a model was vetoed for leaf task execution.
        Returns (vetoed: bool, reason: Optional[str]).
        """
        prof = self._profiles.get(model_name)
        if not prof:
            # Not yet probed, probe now if online
            prof = self.probe_model(model_name)
        if prof and prof.vetoed:
            return True, prof.veto_reason
        return False, None

    def probe_model(self, model_name: str, force_reprobe: bool = False) -> EmpiricalModelProfile:
        """
        Performs empirical testing of the model against the live Ollama daemon:
        1. Context length lookup via /api/show
        2. Fenced emission test
        3. Micro coding fixture test in sandbox
        """
        if not force_reprobe and model_name in self._profiles:
            return self._profiles[model_name]

        import time
        t0 = time.perf_counter()

        # Step 1: Query model info via /api/show
        context_len = 4096
        param_size = "unknown"
        quant_level = "unknown"

        try:
            show_url = f"{self.host}/api/show"
            payload = json.dumps({"name": model_name}).encode("utf-8")
            req = urllib.request.Request(show_url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                details = data.get("details", {})
                param_size = details.get("parameter_size", "unknown")
                quant_level = details.get("quantization_level", "unknown")
                # Parse context length from parameters
                params_str = data.get("parameters", "")
                ctx_match = re.search(r"num_ctx\s+(\d+)", params_str)
                if ctx_match:
                    context_len = int(ctx_match.group(1))
                else:
                    context_len = 32768 if "qwen" in model_name.lower() or "deepseek" in model_name.lower() else 4096
        except Exception:
            pass

        # Step 2: Fenced emission probe
        fenced_ok = False
        raw_output = ""
        try:
            gen_url = f"{self.host}/api/generate"
            gen_payload = json.dumps({
                "model": model_name,
                "prompt": "Write a python function `def ping(): return 'pong'` inside a single markdown python code block.",
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 128},
            }).encode("utf-8")
            gen_req = urllib.request.Request(gen_url, data=gen_payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(gen_req, timeout=5.0) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                raw_output = res_data.get("response", "")
                if "```python" in raw_output and "def ping" in raw_output:
                    fenced_ok = True
                elif "```" in raw_output and "def ping" in raw_output:
                    fenced_ok = True
        except Exception:
            fenced_ok = False

        # Step 3: Micro coding fixture probe in sandbox
        coding_passed = False
        fixture_code = ""
        try:
            fix_payload = json.dumps({
                "model": model_name,
                "prompt": "Write ONLY the python function `def add(a: int, b: int) -> int: return a + b` without any explanation.",
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 128},
            }).encode("utf-8")
            fix_req = urllib.request.Request(gen_url, data=fix_payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(fix_req, timeout=5.0) as resp:
                fix_data = json.loads(resp.read().decode("utf-8"))
                fixture_code = fix_data.get("response", "")
                
                # Extract code block if fenced
                if "```" in fixture_code:
                    match = re.search(r"```(?:python)?\s*([\s\S]*?)\s*```", fixture_code)
                    if match:
                        fixture_code = match.group(1)

                test_script = f"{fixture_code}\n\nassert add(2, 3) == 5\nassert add(-1, 1) == 0\nprint('PASSED')"
                ok, logs = self.sandbox.execute_script(test_script, filename="probe_test.py")
                if ok and "PASSED" in logs:
                    coding_passed = True
        except Exception:
            coding_passed = False

        latency = (time.perf_counter() - t0) * 1000

        # Step 4: Veto logic
        vetoed = False
        veto_reason = None

        if not coding_passed:
            vetoed = True
            veto_reason = f"VETO: Model failed micro coding fixture execution assertion (add(2,3) != 5)."
        elif context_len < 4096:
            vetoed = True
            veto_reason = f"VETO: Context window ({context_len}) is below minimum 4096 token threshold."

        profile = EmpiricalModelProfile(
            model_name=model_name,
            context_length=context_len,
            parameter_size=param_size,
            quantization_level=quant_level,
            fenced_emission_ok=fenced_ok,
            coding_fixture_passed=coding_passed,
            probed_at=time.time(),
            vetoed=vetoed,
            veto_reason=veto_reason,
            response_latency_ms=latency,
        )

        self._profiles[model_name] = profile
        self._persist_cache()
        return profile

    def get_cached_profiles(self) -> Dict[str, EmpiricalModelProfile]:
        """Returns map of all currently cached model profiles."""
        self._load_cache()
        return dict(self._profiles)

    def auto_probe_daemon(self, host: Optional[str] = None) -> Dict[str, EmpiricalModelProfile]:
        """
        Autonomous Probing: Queries live Ollama daemon, discovers all installed models,
        and empirically benchmarks each model in background without waiting for manual probe clicks.
        """
        target_host = (host or self.host).rstrip("/")
        discovered: List[str] = []
        try:
            tags_url = f"{target_host}/api/tags"
            with urllib.request.urlopen(tags_url, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("models", []):
                    name = m.get("name") or m.get("model")
                    if name:
                        discovered.append(name)
        except Exception:
            return self.get_cached_profiles()

        for model_name in discovered:
            # Probe if not already cached within last 24h
            existing = self._profiles.get(model_name)
            if not existing or (time.time() - existing.probed_at > 86400):
                try:
                    self.probe_model(model_name)
                except Exception:
                    pass

        return self.get_cached_profiles()

    def rank_models(self, candidate_models: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Dynamically ranks candidate models by real empirical capability score,
        NOT a static name lookup table.
        """
        self._load_cache()
        models_to_rank = candidate_models if candidate_models is not None else list(self._profiles.keys())
        ranked = []

        for m_name in models_to_rank:
            prof = self._profiles.get(m_name)
            if not prof:
                # If not probed, trigger fast probe
                try:
                    prof = self.probe_model(m_name)
                except Exception:
                    prof = None

            if not prof:
                ranked.append({
                    "model_name": m_name,
                    "score": 10.0,
                    "vetoed": False,
                    "reason": "Unprobed (heuristic fallback)",
                    "fenced_ok": False,
                    "fixture_ok": False,
                    "context_length": 4096,
                    "latency_ms": 100.0,
                })
                continue

            # Compute empirical hardware score
            score = 0.0
            if prof.coding_fixture_passed:
                score += 100.0
            if prof.fenced_emission_ok:
                score += 50.0

            # Reward context window capacity
            score += min(50.0, prof.context_length / 256.0)

            # Penalize response latency
            score -= min(30.0, prof.response_latency_ms / 100.0)

            if prof.vetoed:
                score = -100.0

            ranked.append({
                "model_name": m_name,
                "score": round(score, 1),
                "vetoed": prof.vetoed,
                "veto_reason": prof.veto_reason,
                "fenced_ok": prof.fenced_emission_ok,
                "fixture_ok": prof.coding_fixture_passed,
                "context_length": prof.context_length,
                "latency_ms": round(prof.response_latency_ms, 1),
                "parameter_size": prof.parameter_size,
            })

        ranked.sort(key=lambda x: (not x["vetoed"], x["score"]), reverse=True)
        return ranked
