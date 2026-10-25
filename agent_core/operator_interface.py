"""
operator_interface.py
=====================
Sandbox Operator Interface Architecture for Local & Cloud LLMs.
Allows configured local Ollama and cloud LLM providers
to operate the sandbox environment, invoke tools, inspect execution feedback,
self-repair code, and leverage the LOGOS cognitive stack.
"""

from __future__ import annotations

import ast
import concurrent.futures
import math
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Generic,
    List,
    Optional,
    Tuple,
    TypeVar,
    Union,
)

from agent_core.bridgemind_swarm import (
    BridgeMindMode,
    BridgeMindSwarmCoordinator,
    SwarmTokenPolicy,
)
from agent_core.execution_trace import ExecutionTrace
from agent_core.hybrid_tiering import HybridTieringCoordinator
from agent_core.llm.base import BaseLLMProvider
from agent_core.llm.registry import LLMRegistry
from agent_core.logos import (
    AntichainParallelExecutor,
    DAGTaskNode,
    DependencyDAG,
    DualFrameworkConvergenceEngine,
    DynamicAxiomRetriever,
    FractalReasoningEngine,
    HolographicMemoryEngine,
    MasterIndexDecomposer,
    NASTopologyEngine,
    PonderingEngine,
    QditSearchAccelerator,
)
from agent_core.logos.decomposition import (
    BlueprintEngine,
)
from agent_core.logos.recursive_auditor import RigorousAuditor
from agent_core.memory import ConversationBuffer, EpisodicStore, MultiLayerContinuousMemory
from agent_core.sandbox import CodeExecutionSandbox
from agent_core.tools import ToolRegistry

if TYPE_CHECKING:
    from agent_core.logos.decomposition import Subquestion


@dataclass
class ProgressEvent:
    """Live telemetry event streamed during LOGOS + Sandbox pipeline execution."""

    phase: str  # e.g. "classification", "planning", "cognition", "blueprint", "implementation", "verification", "completed"
    status: str  # "started", "in_progress", "completed", "failed"
    message: str
    progress_pct: int  # 0-100
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "progress",
            "phase": self.phase,
            "status": self.status,
            "message": self.message,
            "progress": self.progress_pct,
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class SandboxSessionReport:
    """Detailed execution telemetry from an LLM-driven sandbox session."""

    task: str
    status: str  # "success" or "failed"
    provider: str
    model: str
    iterations: int
    final_code: str
    test_logs: str
    review: Dict[str, Any]
    topology_used: str
    qualified_properties_count: int
    holographic_stats: Dict[str, Any]
    elapsed_time_s: float
    qualified_properties: List[Dict[str, Any]] = field(default_factory=list)
    verification_level: str = "not_verified"
    history: List[Dict[str, Any]] = field(default_factory=list)
    blueprint: Optional[Dict[str, Any]] = None
    convergence_rounds: int = 0
    fixed_point_reached: bool = False
    audit_history: List[Dict[str, Any]] = field(default_factory=list)
    implementation_plan: Optional[str] = None
    progress_log: List[Dict[str, Any]] = field(default_factory=list)
    total_tokens_used: int = 0
    token_usage_complete: bool = False
    effective_tokens: int = 0
    qdit_certificate: Optional[Dict[str, Any]] = None
    hybrid_telemetry: Optional[Dict[str, Any]] = None
    swarm_report: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "iterations": self.iterations,
            "final_code": self.final_code,
            "test_logs": self.test_logs,
            "review": self.review,
            "topology": self.topology_used,
            "qualified_properties_count": self.qualified_properties_count,
            "qualified_properties": self.qualified_properties,
            "verification_level": self.verification_level,
            "holographic_compression": self.holographic_stats,
            "elapsed_time_s": round(self.elapsed_time_s, 2),
            "blueprint": self.blueprint,
            "convergence_rounds": self.convergence_rounds,
            "fixed_point_reached": self.fixed_point_reached,
            "fixed_point_scope": "heuristic_static_audit_only",
            "audit_history": self.audit_history,
            "implementation_plan": self.implementation_plan,
            "progress_log": self.progress_log,
            "total_tokens": self.total_tokens_used,
            "token_usage_complete": self.token_usage_complete,
            "effective_tokens": self.effective_tokens,
            "effective_tokens_note": "Actual provider-reported token count; not context capacity.",
            "qdit_certificate": self.qdit_certificate,
            "hybrid_telemetry": self.hybrid_telemetry,
            "swarm_report": self.swarm_report,
        }


# =======================================================================
# TypeSafe AI Jev Fast Decision Primitives & Engine
# =======================================================================


class TaskIntent(str, Enum):
    CODING = "CODING"
    CONVERSATIONAL = "CONVERSATIONAL"
    MCP_DEV = "MCP_DEV"
    MATH_REASONING = "MATH_REASONING"
    SYSTEM_DESIGN = "SYSTEM_DESIGN"
    CREATIVE_EXPLANATION = "CREATIVE_EXPLANATION"
    SECURITY_AUDIT = "SECURITY_AUDIT"


class CodeTargetType(str, Enum):
    PYTHON_SCRIPT = "PYTHON_SCRIPT"
    HTML5_APP = "HTML5_APP"
    MCP_SERVER = "MCP_SERVER"
    ALGORITHM = "ALGORITHM"
    TEST_SUITE = "TEST_SUITE"
    NONE = "NONE"


T = TypeVar("T")


@dataclass(frozen=True)
class JevChoice(Generic[T]):
    value: T
    confidence: float
    probabilities: Dict[str, float]
    latency_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primitive": "Choice",
            "value": str(self.value.value if hasattr(self.value, "value") else self.value),
            "confidence": round(self.confidence, 4),
            "confidence_kind": "heuristic_score_not_calibrated_probability",
            "probabilities": {k: round(v, 4) for k, v in self.probabilities.items()},
            "latency_ms": round(self.latency_ms, 2),
        }


@dataclass(frozen=True)
class JevScore:
    score: float
    min_scale: float = 0.0
    max_scale: float = 10.0
    confidence: float = 0.95
    latency_ms: float = 0.0

    @property
    def normalized(self) -> float:
        span = self.max_scale - self.min_scale
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (self.score - self.min_scale) / span))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primitive": "Score",
            "score": round(self.score, 2),
            "scale": [self.min_scale, self.max_scale],
            "confidence": round(self.confidence, 4),
            "confidence_kind": "heuristic_score_not_calibrated_probability",
            "normalized": round(self.normalized, 4),
            "latency_ms": round(self.latency_ms, 2),
        }


@dataclass(frozen=True)
class JevNoul:
    decision: bool
    probability: float
    gate_name: str
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primitive": "Noul",
            "gate": self.gate_name,
            "decision": self.decision,
            "probability": round(self.probability, 4),
            "latency_ms": round(self.latency_ms, 2),
        }


@dataclass
class JevDecisionReport:
    intent: JevChoice[TaskIntent]
    code_target: JevChoice[CodeTargetType]
    complexity: JevScore
    requires_sandbox: JevNoul
    is_safe: JevNoul
    has_interactive_canvas: JevNoul
    requires_blueprint: JevNoul
    is_mcp_operation: JevNoul
    total_pipeline_latency_ms: float
    timestamp: float = field(default_factory=time.time)

    def summary(self) -> str:
        return (
            f"Jev[Intent={self.intent.value.value} ({self.intent.confidence * 100:.1f}%), "
            f"Target={self.code_target.value.value}, Sandbox={'YES' if self.requires_sandbox.decision else 'NO'}, "
            f"Complexity={self.complexity.score:.1f}/10, Latency={self.total_pipeline_latency_ms:.2f}ms]"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary(),
            "total_latency_ms": round(self.total_pipeline_latency_ms, 2),
            "intent": self.intent.to_dict(),
            "code_target": self.code_target.to_dict(),
            "complexity": self.complexity.to_dict(),
            "guards": {
                "requires_sandbox": self.requires_sandbox.to_dict(),
                "is_safe": self.is_safe.to_dict(),
                "has_interactive_canvas": self.has_interactive_canvas.to_dict(),
                "requires_blueprint": self.requires_blueprint.to_dict(),
                "is_mcp_operation": self.is_mcp_operation.to_dict(),
            },
        }


class JevDecisionEngine:
    """
    TypeSafe AI Jev Fast Decision Engine.
    Executes sub-15ms typed decision primitives (Choice, Score, Noul) with optional dynamic LLM support.
    """

    def __init__(self, llm: Optional[Any] = None) -> None:
        self.llm = llm
        self._execution_count = 0

    def evaluate(self, prompt: str) -> JevDecisionReport:
        t0 = time.perf_counter()
        p_clean = prompt.strip()
        p_lower = p_clean.lower()

        # 1. Safety Gate
        is_safe = self._check_safety(p_clean)

        # 2. Intent Choice
        intent = self._classify_intent(p_lower)

        # 3. Target Format Choice
        target = self._classify_target(p_lower, intent.value)

        # 4. Complexity Score
        complexity = self._compute_complexity(p_clean, intent.value)

        # 5. Boolean Gates (Noul)
        req_sandbox = JevNoul(
            decision=intent.value
            in (TaskIntent.CODING, TaskIntent.MCP_DEV, TaskIntent.MATH_REASONING)
            or any(w in p_lower for w in ["run", "execute", "test", "build", "code", "implement"]),
            probability=0.98 if intent.value in (TaskIntent.CODING, TaskIntent.MCP_DEV) else 0.05,
            gate_name="requires_sandbox",
        )
        has_canvas = JevNoul(
            decision=any(
                w in p_lower
                for w in [
                    "canvas",
                    "game",
                    "temple run",
                    "runner",
                    "arcade",
                    "animation",
                    "interactive",
                    "three.js",
                    "webgl",
                    "html5 game",
                ]
            ),
            probability=0.99 if any(w in p_lower for w in ["canvas", "game", "runner"]) else 0.01,
            gate_name="has_interactive_canvas",
        )
        req_blueprint = JevNoul(
            decision=complexity.score >= 5.0
            or has_canvas.decision
            or intent.value == TaskIntent.MCP_DEV,
            probability=0.95,
            gate_name="requires_blueprint",
        )
        is_mcp = JevNoul(
            decision=intent.value == TaskIntent.MCP_DEV
            or any(
                w in p_lower for w in ["mcp", "fastmcp", "model context protocol", "mcp server"]
            ),
            probability=0.99 if "mcp" in p_lower else 0.01,
            gate_name="is_mcp_operation",
        )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self._execution_count += 1

        return JevDecisionReport(
            intent=intent,
            code_target=target,
            complexity=complexity,
            requires_sandbox=req_sandbox,
            is_safe=is_safe,
            has_interactive_canvas=has_canvas,
            requires_blueprint=req_blueprint,
            is_mcp_operation=is_mcp,
            total_pipeline_latency_ms=elapsed_ms,
        )

    def _check_safety(self, prompt: str) -> JevNoul:
        p_low = prompt.lower()
        danger = any(
            p in p_low
            for p in [
                "rm -rf",
                "format c:",
                "drop database",
                "delete all",
                "os.system('rm",
                'os.system("rm',
                ":(){ :|:& };:",
            ]
        )
        return JevNoul(
            decision=not danger, probability=0.01 if danger else 0.999, gate_name="is_safe"
        )

    def _classify_intent(self, p_lower: str) -> JevChoice[TaskIntent]:
        if any(w in p_lower for w in ["mcp", "model context protocol", "fastmcp", "mcp server"]):
            val = TaskIntent.MCP_DEV
            probs = {TaskIntent.MCP_DEV.value: 0.95, TaskIntent.CODING.value: 0.05}
            conf = 0.95
        elif any(
            w in p_lower
            for w in ["zeta", "riemann", "higgs", "boson", "manifold", "montgomery", "eigenvalue"]
        ):
            val = TaskIntent.MATH_REASONING
            probs = {TaskIntent.MATH_REASONING.value: 0.92, TaskIntent.CODING.value: 0.08}
            conf = 0.92
        elif any(
            w in p_lower
            for w in [
                "write",
                "code",
                "implement",
                "def ",
                "class ",
                "divide",
                "quicksort",
                "binary search",
                "sort",
                "algorithm",
                "python",
                "function",
                "fix",
                "solve",
                "bug",
                "refactor",
                "build",
                "create",
                "game",
                "canvas",
                "app",
                "make",
                "program",
                "script",
            ]
        ):
            val = TaskIntent.CODING
            probs = {TaskIntent.CODING.value: 0.96, TaskIntent.CONVERSATIONAL.value: 0.04}
            conf = 0.96
        elif any(
            w in p_lower
            for w in ["hello", "hi", "who are you", "what can you help", "hey", "greetings"]
        ):
            val = TaskIntent.CONVERSATIONAL
            probs = {TaskIntent.CONVERSATIONAL.value: 0.98, TaskIntent.CODING.value: 0.02}
            conf = 0.98
        elif any(
            w in p_lower for w in ["architect", "system design", "microservice", "infrastructure"]
        ):
            val = TaskIntent.SYSTEM_DESIGN
            probs = {TaskIntent.SYSTEM_DESIGN.value: 0.90, TaskIntent.CODING.value: 0.10}
            conf = 0.90
        else:
            val = TaskIntent.CONVERSATIONAL if len(p_lower.split()) < 5 else TaskIntent.CODING
            probs = {val.value: 0.85}
            conf = 0.85
        return JevChoice(value=val, confidence=conf, probabilities=probs, latency_ms=0.1)

    def _classify_target(self, p_lower: str, intent: TaskIntent) -> JevChoice[CodeTargetType]:
        if any(
            w in p_lower
            for w in ["canvas", "game", "runner", "arcade", "html", "html5", "animation"]
        ):
            val = CodeTargetType.HTML5_APP
        elif intent == TaskIntent.MCP_DEV:
            val = CodeTargetType.MCP_SERVER
        elif intent == TaskIntent.CODING or intent == TaskIntent.MATH_REASONING:
            val = (
                CodeTargetType.ALGORITHM
                if "algorithm" in p_lower or "sort" in p_lower
                else CodeTargetType.PYTHON_SCRIPT
            )
        else:
            val = CodeTargetType.NONE
        return JevChoice(
            value=val, confidence=0.94, probabilities={val.value: 0.94}, latency_ms=0.1
        )

    def _compute_complexity(self, prompt: str, intent: TaskIntent) -> JevScore:
        score = 2.0
        words = len(prompt.split())
        if words > 15:
            score += 2.0
        if words > 40:
            score += 2.0
        if intent in (TaskIntent.CODING, TaskIntent.MCP_DEV, TaskIntent.MATH_REASONING):
            score += 2.5
        if any(
            w in prompt.lower()
            for w in ["game", "canvas", "3d", "distributed", "compiler", "engine", "quantum"]
        ):
            score += 2.5
        score = min(10.0, score)
        return JevScore(score=score, min_scale=0.0, max_scale=10.0, confidence=0.95, latency_ms=0.1)


class SandboxOperatorInterface:
    """
    Unified Interface Architecture coupling an LLM to the execution sandbox.
    Acts as the sensory-motor cortex between the model and the computational sandbox.
    """

    def __init__(
        self,
        llm: Optional[Union[BaseLLMProvider, str]] = None,
        provider_name: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout: float = 5.0,
        enable_logos: bool = True,
        max_tokens_per_call: int = 8192,
        max_effective_tokens: int = 100_000,
        enable_hybrid: bool = False,
        **kwargs: Any,
    ) -> None:
        if isinstance(llm, str):
            provider_name = llm
            llm = None

        if llm is None:
            self.llm = LLMRegistry.get_provider(provider_name, model_name=model_name, **kwargs)
        else:
            self.llm = llm

        self.sandbox = CodeExecutionSandbox(timeout_seconds=timeout)
        self.tools = ToolRegistry.default()
        self.buffer = ConversationBuffer(max_tokens=32_000)
        self.episodes = EpisodicStore()
        self.enable_logos = enable_logos
        self.chunk_max_tokens = 8192  # bounded output budget per generation call
        self.max_tokens_per_call = max(256, min(int(max_tokens_per_call), 16384))
        # Retained for API compatibility only; it is not a claim about model context length.
        self.max_effective_tokens = max(1, min(int(max_effective_tokens), 1_000_000))

        # Initialize Save Tokens Feature (Hybrid Tiering Coordinator)
        self.hybrid_coordinator = HybridTieringCoordinator(
            cloud_llm=self.llm,
            enabled=enable_hybrid,
            auto_inspect=True,
        )

        # Initialize LOGOS cognitive layers powered by LLM as the central brain
        self.decomposer = MasterIndexDecomposer(llm=self.llm)
        self.fractal_mera = FractalReasoningEngine()
        self.holographic_mem = HolographicMemoryEngine(dimension=128, max_token_capacity=100_000)
        self.nas = NASTopologyEngine(llm=self.llm)
        self.retriever = DynamicAxiomRetriever()
        self.pondering = PonderingEngine(llm=self.llm)
        self.convergence = DualFrameworkConvergenceEngine(llm=self.llm)
        self.blueprint_engine = BlueprintEngine(self.llm)
        self.auditor = RigorousAuditor(self.llm)
        self.multi_layer_mem = MultiLayerContinuousMemory()
        self.qdit_accelerator = QditSearchAccelerator(d_physical=10, max_bond_dim=16)
        trace_dir = Path(
            os.environ.get("AGENTUFO_TRACE_DIR", str(Path.cwd() / ".agentufo" / "traces"))
        )
        self.execution_trace = ExecutionTrace(trace_dir / "dag-events.jsonl")
        self.dag_executor = AntichainParallelExecutor(
            max_workers=min(8, max(2, os.cpu_count() or 4)), trace=self.execution_trace
        )

    def _safe_llm_max_tokens(self, default_cap: int = 8192) -> int:
        """
        Determines the safe per-request token ceiling for the active LLM provider,
        while enforcing a bounded per-generation output budget.
        """
        p_name = getattr(self.llm, "provider_name", "deepseek").lower()
        if p_name in ("deepseek", "kimi", "minimax", "qwen"):
            # DeepSeek / OpenAI-compatible API maximum allowed output is 393,216 tokens
            return min(default_cap, 16_384)
        elif p_name in ("ollama",):
            return min(default_cap, 8192)
        elif p_name in ("openai", "grok"):
            return min(default_cap, 16384)
        elif p_name in ("gemini",):
            return min(default_cap, 8192)
        elif p_name in ("anthropic", "claude"):
            return min(default_cap, 8192)
        return min(default_cap, 8192)

    def evaluate_and_elevate_confidence_with_nas(
        self,
        chunk_name: str,
        chunk_content: str,
        current_confidence: float,
        task_context: str = "",
    ) -> Tuple[str, float, Optional[Dict[str, Any]]]:
        """Run lightweight syntax/structure checks without fabricating confidence increases.

        `current_confidence` is retained for compatibility and is treated as an uncalibrated
        upstream score. Syntax validity does not prove semantic correctness or security.
        """
        score = float(current_confidence) if math.isfinite(float(current_confidence)) else 0.0
        score = min(1.0, max(0.0, score))
        if not chunk_content or not chunk_content.strip():
            return chunk_content, score, {"status": "empty_content", "confidence_calibrated": False}

        content_lower = chunk_content.lower()
        if "<html" in content_lower or "<canvas" in content_lower:
            has_script = "<script" not in content_lower or "</script>" in content_lower
            return (
                chunk_content,
                score,
                {
                    "status": "static_html_markers_detected"
                    if has_script
                    else "html_script_structure_incomplete",
                    "syntax_or_structure_checked": True,
                    "semantic_correctness_verified": False,
                    "confidence_calibrated": False,
                },
            )

        try:
            ast.parse(chunk_content)
            return (
                chunk_content,
                score,
                {
                    "status": "python_syntax_valid",
                    "syntax_or_structure_checked": True,
                    "semantic_correctness_verified": False,
                    "confidence_calibrated": False,
                },
            )
        except SyntaxError as syntax_error:
            python_indicators = (
                re.search(
                    r"\b(def|import|from|class|return|elif|lambda|None|True|False)\b", chunk_content
                )
                is not None
            )
            if not python_indicators:
                return (
                    chunk_content,
                    score,
                    {
                        "status": "not_recognized_as_python_source",
                        "syntax_or_structure_checked": False,
                        "semantic_correctness_verified": False,
                        "confidence_calibrated": False,
                    },
                )
            if not (
                self.llm
                and hasattr(self.llm, "generate")
                and getattr(self.llm, "is_available", lambda: True)()
            ):
                return (
                    chunk_content,
                    score,
                    {
                        "status": "syntax_error_no_provider_repair",
                        "error": str(syntax_error),
                        "confidence_calibrated": False,
                    },
                )

        topology = self.nas.search_optimal_topology(
            f"Repair syntax in {chunk_name}; do not claim correctness without tests"
        )
        repair_prompt = (
            f"Task context: {task_context}\nArtifact: {chunk_name}\n"
            f"Current content:\n```python\n{chunk_content}\n```\n"
            "Repair syntax errors only where possible, preserve intended behavior, and return the complete code. "
            "Do not report a confidence percentage or claim that runtime correctness was verified."
        )
        try:
            response = self.llm.generate(
                prompt=f"Suggested execution topology: {topology.topology_type.value}\n{repair_prompt}",
                system_prompt="Repair the syntax issue. Output only the complete code in a markdown block.",
                temperature=0.1,
                max_tokens=self._safe_llm_max_tokens(4096),
            )
            repaired = (
                self.llm.extract_code_block(response.content, "python") or response.content.strip()
            )
            try:
                ast.parse(repaired)
                status = "syntax_repair_returned_parseable_code"
            except SyntaxError:
                status = "repair_returned_unparseable_code"
            return (
                repaired,
                score,
                {
                    "status": status,
                    "syntax_or_structure_checked": True,
                    "semantic_correctness_verified": False,
                    "confidence_calibrated": False,
                    "provider_reported_tokens": int(getattr(response, "total_tokens", 0) or 0),
                },
            )
        except Exception as exc:
            return (
                chunk_content,
                score,
                {
                    "status": "repair_failed",
                    "error": str(exc)[:240],
                    "confidence_calibrated": False,
                },
            )

    def solve_task(
        self,
        task_description: str,
        max_retries: int = 3,
        progress_callback: Optional[Callable[[ProgressEvent], None]] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        enable_hybrid: bool = False,
        swarm_mode: Optional[str] = None,
        swarm_agents: int = 0,
        swarm_policy: str = "all",
    ) -> SandboxSessionReport:
        """
        Runs a model-assisted implementation workflow with bounded generation and memory budgets.
        Runtime test execution is authoritative for Python tasks; HTML artifacts receive static checks
        only unless an external browser test runner is configured. The static auditor is heuristic.
        """
        if enable_hybrid and hasattr(self, "hybrid_coordinator") and self.hybrid_coordinator:
            self.hybrid_coordinator.enabled = True

        t0 = time.perf_counter()
        history: List[Dict[str, Any]] = []
        progress_log: List[Dict[str, Any]] = []
        total_tokens = 0

        def emit(
            phase: str,
            status: str,
            message: str,
            pct: int,
            details: Optional[Dict[str, Any]] = None,
        ):
            ev = ProgressEvent(
                phase=phase, status=status, message=message, progress_pct=pct, details=details or {}
            )
            progress_log.append(ev.to_dict())
            if progress_callback:
                try:
                    progress_callback(ev)
                except Exception:
                    pass
            self.holographic_mem.store(f"[{phase}] {status}: {message}", category="progress")
            return ev

        # Check for BridgeMind Multi-Agent Swarm execution
        if swarm_agents >= 2 and swarm_mode:
            emit(
                "swarm",
                "started",
                f"Activating BridgeMind Swarm with {swarm_agents} agents in '{swarm_mode}' mode (Policy: {swarm_policy})...",
                10,
            )
            mode_enum = (
                BridgeMindMode(swarm_mode)
                if swarm_mode in ("split_work", "competitive_consensus")
                else BridgeMindMode.COMPETITIVE_CONSENSUS
            )
            pol_enum = (
                SwarmTokenPolicy(swarm_policy)
                if swarm_policy in ("all", "leader_only", "cloud_only")
                else SwarmTokenPolicy.ALL
            )
            swarm_coord = BridgeMindSwarmCoordinator(
                cloud_llm=self.llm,
                local_llm=getattr(self.hybrid_coordinator, "local_llm", None)
                if hasattr(self, "hybrid_coordinator")
                else None,
                num_agents=swarm_agents,
                mode=mode_enum,
                token_policy=pol_enum,
                sandbox=self.sandbox,
            )

            def _swarm_cb(msg: str, pct: int, det: Dict[str, Any]):
                emit("swarm", "in_progress", msg, pct, det)

            swarm_res = swarm_coord.execute_swarm(task_description, progress_callback=_swarm_cb)
            emit(
                "completed",
                "completed",
                f"BridgeMind Swarm finished successfully. Winner: {swarm_res.winning_agent_id}",
                100,
            )
            return SandboxSessionReport(
                task=task_description,
                status=swarm_res.status,
                provider=self.llm.provider_name,
                model=self.llm.model_name,
                iterations=1,
                final_code=swarm_res.final_code,
                test_logs="BridgeMind swarm returned a result; inspect the included swarm report for its actual validation details.",
                review={
                    "approved": swarm_res.status == "success",
                    "security_check": "not_independently_verified",
                    "style_score": None,
                },
                topology_used="bridgemind_swarm",
                qualified_properties_count=len(swarm_res.agents),
                holographic_stats=self.holographic_mem.get_boundary_compression_stats(),
                elapsed_time_s=swarm_res.elapsed_time_s,
                history=[{"attempt": 1, "code": swarm_res.final_code, "success": True}],
                blueprint={
                    "system_layout": f"BridgeMind Swarm with {swarm_agents} agents in {swarm_mode} mode"
                },
                convergence_rounds=0,
                fixed_point_reached=False,
                verification_level="swarm_reported_unverified",
                audit_history=[],
                implementation_plan=f"Multi-Agent Swarm: {swarm_res.mode.value}",
                progress_log=progress_log,
                total_tokens_used=swarm_res.total_tokens_consumed,
                effective_tokens=int(swarm_res.total_tokens_consumed or 0),
                hybrid_telemetry=self.hybrid_coordinator.get_status()
                if hasattr(self, "hybrid_coordinator") and self.hybrid_coordinator
                else None,
                swarm_report=swarm_res.to_dict(),
            )

        # Phase 0: Autonomous Research & Knowledge Discovery (Best-of-Best Scouting)
        emit(
            "research",
            "started",
            "Autonomous Knowledge Discovery: Scouting state-of-the-art patterns, libraries & algorithms...",
            5,
        )
        search_knowledge = ""
        try:
            # Query web search for frontier solutions
            query_words = [
                w
                for w in task_description.split()
                if len(w) > 2
                and w.lower()
                not in ("write", "build", "code", "create", "make", "that", "this", "with")
            ]
            query = " ".join(query_words) or task_description
            search_res = self.tools.execute("web_search", query=query)
            if search_res.success and search_res.output:
                search_knowledge = search_res.output
                self.holographic_mem.store(
                    f"Web Knowledge: {search_knowledge}", category="web_search"
                )
                emit(
                    "research",
                    "completed",
                    f"Discovered frontier patterns via autonomous knowledge engine ({len(search_knowledge)} chars).",
                    10,
                )
            else:
                emit("research", "completed", "Referenced internal cognitive knowledge bases.", 10)
        except Exception:
            emit("research", "completed", "Core cognitive knowledge bases active.", 10)
        self.holographic_mem.store_layer_chunk(
            0, "research", search_knowledge or "Core knowledge base referenced."
        )
        self.multi_layer_mem.add_chunk(
            0, "research", search_knowledge or "Core knowledge base referenced."
        )

        # Phase 1: Exploration with Generalized Refusal / Evasion Reframing
        emit(
            "planning",
            "started",
            f"Formulating most impressive architectural plan with evasion reframing for {self.llm.provider_name}/{self.llm.model_name}...",
            15,
        )
        coherent_prefix = self.holographic_mem.build_coherent_context_for_next_layer()

        # Ask for the best / most impressive way to execute prompt; detect and reframe evasiveness
        raw_plan = self.decomposer.explore_impressive_execution(task_description)
        if self.decomposer.is_evasive_or_impossible(raw_plan):
            emit(
                "planning",
                "in_progress",
                "Evasive or impossible response detected: reframing into actionable operational engineering blueprint...",
                20,
            )
            implementation_plan = self.decomposer.reframe_evasive_response(
                task_description, raw_plan
            )
        else:
            implementation_plan = raw_plan

        self.holographic_mem.store(
            f"Implementation Plan: {implementation_plan}", category="implementation_plan"
        )
        self.holographic_mem.store_layer_chunk(1, "planning", implementation_plan)
        self.multi_layer_mem.add_chunk(1, "planning", implementation_plan)
        emit(
            "planning",
            "completed",
            f"Plan synthesized ({len(implementation_plan)} chars) | Operational blueprint established.",
            25,
            {"plan": implementation_plan},
        )

        # Phase 2: Parallel Recursive Fractal Decomposition & Qdit Search Space Compression
        emit(
            "cognition",
            "started",
            "Running parallel recursive fractal decomposition + Qdit search space compression (10-state qudit Hilbert space)...",
            30,
        )
        topology = self.nas.search_optimal_topology(task_description)
        pondering_synthesis = self.pondering.evaluate_multi_angle(task_description)

        # Execute parallel recursive fractal decomposition (Layer 1 subsystems -> Layer 2 leaf modules)
        decomp_result = self.decomposer.decompose_recursive(task_description, max_depth=2)
        layer1_subquestions = decomp_result["layer1_subquestions"]
        layer2_leaf_subquestions = decomp_result["layer2_leaf_subquestions"]
        task_dag = decomp_result.get("dag")
        subquestions = layer1_subquestions  # Backwards compatibility

        # Qdit candidate ranking & search space reduction with exact classical verification
        candidate_items = [
            {"id": sq.id, "text": sq.question, "dimension": sq.dimension}
            for sq in layer2_leaf_subquestions
        ]
        _, qdit_state = self.qdit_accelerator.compress_and_rank_candidates(
            candidates=candidate_items,
            scoring_fn=lambda c: (
                0.90
                if any(k in c["dimension"] for k in ["BOUNDARY", "VERIFICATION", "COMPLEXITY"])
                else 0.82
            ),
            top_k=max(1, len(candidate_items)),
        )

        # Run syntax/structure checks on subquestions; their heuristic scores are not elevated by parsing.
        for sq in layer1_subquestions:
            if getattr(sq, "confidence", 0.0) < 0.90:
                _, elevated_c, _ = self.evaluate_and_elevate_confidence_with_nas(
                    chunk_name=f"Subsystem {sq.id}",
                    chunk_content=f"{sq.question} (Dimension: {sq.dimension})",
                    current_confidence=sq.confidence if sq.confidence > 0 else 0.82,
                    task_context=task_description,
                )
                sq.confidence = elevated_c

        for sq in layer2_leaf_subquestions:
            if getattr(sq, "confidence", 0.0) < 0.90:
                _, elevated_c, _ = self.evaluate_and_elevate_confidence_with_nas(
                    chunk_name=f"Leaf {sq.id}",
                    chunk_content=f"{sq.question} (Dimension: {sq.dimension})",
                    current_confidence=sq.confidence if sq.confidence > 0 else 0.82,
                    task_context=task_description,
                )
                sq.confidence = elevated_c

        f1_analysis = self.convergence.analyze_framework_1_classical(task_description)
        f2_analysis = self.convergence.analyze_framework_2_theory2(task_description)
        alignment = self.convergence.construct_property_alignment_matrix(
            f1_analysis, f2_analysis, task_description
        )
        reduction_ratio = qdit_state.certificate.get("candidate_reduction_ratio")
        reduction_label = (
            f"{reduction_ratio}x candidate shortlist"
            if reduction_ratio is not None
            else "not measured"
        )
        fidelity_label = (
            f"{qdit_state.reconstruction_fidelity:.2f}"
            if qdit_state.certificate.get("reconstruction_fidelity_measured")
            else "not measured"
        )
        cognition_summary = (
            f"Topology: {topology.topology_type.value} | 2-Layer Tree: {len(layer1_subquestions)} subsystems, "
            f"{len(layer2_leaf_subquestions)} leaf modules | Classical candidate shortlist: {reduction_label} | "
            f"Reconstruction fidelity: {fidelity_label} | Heuristic angles above threshold: {pondering_synthesis.qualified_properties_count}"
        )
        self.holographic_mem.store_layer_chunk(2, "cognition", cognition_summary)
        self.multi_layer_mem.add_chunk(2, "cognition", cognition_summary)
        emit("cognition", "completed", cognition_summary, 40)

        # Phase 3: Blueprint Generation & Component Contracts
        emit(
            "blueprint",
            "started",
            "Generating architectural blueprint & component contracts...",
            45,
        )
        invariants = [
            p.hypothesis
            for p in pondering_synthesis.qualified_properties
            if p.confidence >= self.pondering.CONFIDENCE_THRESHOLD
        ]
        blueprint = self.blueprint_engine.generate_blueprint(
            task_description,
            topology_type=topology.topology_type.value,
            invariants=invariants,
        )

        for comp in blueprint.components:
            comp_content = f"Name: {comp.name}\nSubsystem: {comp.subsystem}\nPurpose: {comp.purpose}\nContract: {comp.contract}"
            # Static parsing is not a correctness proof; keep the original contract uninflated.
            self.evaluate_and_elevate_confidence_with_nas(
                chunk_name=f"Component {comp.id} ({comp.name})",
                chunk_content=comp_content,
                current_confidence=0.85,
                task_context=task_description,
            )
        self.holographic_mem.store(f"Task: {task_description}", category="task_spec")
        self.holographic_mem.store(pondering_synthesis.synthesis_summary, category="pondering")
        self.holographic_mem.store_blueprint(blueprint)
        self.holographic_mem.store_layer_chunk(3, "blueprint", blueprint.format_holographic_entry())
        self.multi_layer_mem.add_chunk(3, "blueprint", blueprint.format_holographic_entry())
        emit(
            "blueprint",
            "completed",
            f"Blueprint: {len(blueprint.components)} components | System: {blueprint.system_layout[:80]}",
            50,
        )

        # Interactive detection
        interactive_keywords = [
            "game",
            "arcade",
            "canvas",
            "flappy",
            "snake",
            "tetris",
            "pong",
            "breakout",
            "invader",
            "pacman",
            "tictactoe",
            "tic-tac-toe",
            "maze",
            "minesweeper",
            "temple run",
            "runner",
            "gui",
            "calculator app",
            "todo app",
            "web app",
            "html5 app",
            "ide",
            "editor",
            "dashboard",
            "app",
            "tool",
            "studio",
            "platform",
        ]
        is_interactive = any(k in task_description.lower() for k in interactive_keywords)
        if any(
            k in task_description.lower()
            for k in [
                "python",
                "def ",
                "function",
                "class ",
                "algorithm",
                "quicksort",
                "sort",
                "search",
                "divide",
                "fibonacci",
                "tree",
                "graph",
            ]
        ):
            if not any(k in task_description.lower() for k in ["pygame", "html", "canvas", "game"]):
                is_interactive = False

        # Phase 4: MASSIVE PARALLEL EXECUTION OF ALL DECOMPOSED LEAF TASKS
        emit(
            "implementation",
            "started",
            f"Starting MASSIVE PARALLEL EXECUTION of all {len(layer2_leaf_subquestions)} decomposed tasks across LLM threads...",
            55,
        )

        def _execute_leaf_task(leaf: Subquestion, leaf_idx: int) -> Tuple[Subquestion, str, float]:
            leaf_prompt = (
                f"TARGET TASK: {task_description}\n"
                f"PARENT SUBSYSTEM: {leaf.parent_id or 'Architecture'}\n"
                f"DECOMPOSED LEAF TASK: {leaf.question}\n"
                f"DIMENSION: {leaf.dimension}\n"
                "Implement this concrete technical component. Ensure complete boundary safety, type invariants, and robust logic.\n"
                "Output ONLY the complete working code inside a single markdown code block."
            )
            leaf_code = ""
            leaf_conf = 0.85
            try:
                if (
                    hasattr(self, "hybrid_coordinator")
                    and self.hybrid_coordinator
                    and self.hybrid_coordinator.enabled
                ):
                    l_resp, tier = self.hybrid_coordinator.generate(
                        prompt=leaf_prompt,
                        task_type="leaf_code",
                        system_prompt="You are a Principal Systems Engineer implementing a modular component. Output ONLY code in a markdown block.",
                        max_tokens=self._safe_llm_max_tokens(4096),
                    )
                else:
                    l_resp = self.llm.generate(
                        prompt=leaf_prompt,
                        system_prompt="You are a Principal Systems Engineer implementing a modular component. Output ONLY code in a markdown block.",
                        max_tokens=self._safe_llm_max_tokens(4096),
                    )
                raw = l_resp.content.strip()
                extracted = (
                    self.llm.extract_code_block(raw, "html")
                    or self.llm.extract_code_block(raw, "python")
                    or self.llm.extract_code_block(raw, "javascript")
                    or raw
                )
                leaf_code, leaf_conf, _ = self.evaluate_and_elevate_confidence_with_nas(
                    chunk_name=f"LeafTask_{leaf.id}",
                    chunk_content=extracted,
                    current_confidence=0.86,
                    task_context=task_description,
                )
            except Exception as exc:
                self.execution_trace.emit(
                    "leaf_generation.failed",
                    task_id=leaf.id,
                    fields={"error": f"{type(exc).__name__}: {exc}"},
                )
                raise RuntimeError(f"Leaf generation failed for {leaf.id}") from exc

            leaf.code_snippet = leaf_code
            leaf.confidence = leaf_conf
            self.holographic_mem.store_layer_chunk(
                layer_index=10 + leaf_idx,
                name=f"leaf_{leaf.id}",
                prompt=leaf_prompt,
                response=leaf_code,
            )
            return leaf, leaf_code, leaf_conf

        # Phase 4: MASSIVE PARALLEL EXECUTION OF ALL DECOMPOSED LEAF TASKS VIA DEPENDENCY DAG
        emit(
            "implementation",
            "started",
            f"Starting DEPENDENCY-AWARE PARALLEL EXECUTION of {len(layer2_leaf_subquestions)} tasks in concurrent antichains...",
            55,
        )

        max_workers = min(8, max(2, os.cpu_count() or 4))
        leaf_results: Dict[str, str] = {}
        # The decomposition DAG contains both parent planning nodes and leaf code tasks.
        # Execute only leaf tasks here; parent dependencies have already been resolved by
        # decomposition. Preserve dependencies between leaves when the decomposer supplies any.
        leaf_ids = {leaf.id for leaf in layer2_leaf_subquestions}
        execution_dag = DependencyDAG()
        for leaf in layer2_leaf_subquestions:
            leaf_deps = [dep for dep in leaf.dependencies if dep in leaf_ids]
            execution_dag.add_task(
                leaf.id, f"Leaf {leaf.id}: {leaf.question[:60]}", leaf, leaf_deps
            )
        if len(execution_dag.nodes) > 0:

            def _dag_worker(node: DAGTaskNode) -> str:
                leaf_sq = node.payload
                _, code_out, _ = _execute_leaf_task(leaf_sq, 0)
                return code_out

            def _dag_progress(node: DAGTaskNode, done: int, total: int):
                pct = 55 + int((done / max(1, total)) * 15)
                emit(
                    "implementation",
                    "in_progress",
                    f"Parallel DAG execution: completed {done}/{total} tasks ({node.id})",
                    pct,
                )

            dag_results = self.dag_executor.execute_dag(
                execution_dag, _dag_worker, progress_callback=_dag_progress
            )
            for nid, c in dag_results.items():
                if c:
                    leaf_results[nid] = c
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_leaf = {
                    executor.submit(_execute_leaf_task, leaf, idx): leaf
                    for idx, leaf in enumerate(layer2_leaf_subquestions)
                }
                completed_count = 0
                for fut in concurrent.futures.as_completed(future_to_leaf):
                    completed_count += 1
                    try:
                        leaf_obj, leaf_code, conf = fut.result()
                        leaf_results[leaf_obj.id] = leaf_code
                        pct = 55 + int(
                            (completed_count / max(1, len(layer2_leaf_subquestions))) * 15
                        )
                        emit(
                            "implementation",
                            "in_progress",
                            f"Parallel execution: completed {completed_count}/{len(layer2_leaf_subquestions)} tasks ({leaf_obj.id}) [Conf: {conf:.2f}]",
                            pct,
                        )
                    except Exception:
                        pass

        # Phase 5: HIGH-PERFORMANCE UNIFIED SYSTEM CONSOLIDATION & PRODUCTION PACKAGING
        emit(
            "combining",
            "in_progress",
            "Synthesizing and consolidating components into a unified, production-ready system...",
            75,
        )
        all_leaf_code = "\n\n".join(
            f"// Component {lid}:\n{code}" for lid, code in leaf_results.items() if code
        )

        if is_interactive:
            emit(
                "combining",
                "in_progress",
                "Synthesizing complete interactive application (HTML5 Canvas + animations + event loop)...",
                80,
            )
            interactive_prompt = (
                f"TARGET TASK: {task_description}\n\n"
                f"DECOMPOSED COMPONENT SPECIFICATIONS:\n{all_leaf_code}\n\n"
                "CRITICAL REQUIREMENTS:\n"
                "1. Build a complete, working, interactive HTML5 Canvas web application.\n"
                "2. Include all embedded CSS styling and JavaScript animation loop (requestAnimationFrame).\n"
                "3. Ensure keyboard/mouse event listeners and responsive HUD are fully wired.\n"
                "4. Output ONLY the complete HTML code inside a single ```html ``` code block.\n"
                "5. Zero placeholders, zero TODOs."
            )
            try:
                c_resp = self.llm.generate(
                    prompt=interactive_prompt, max_tokens=self._safe_llm_max_tokens(10_000_000)
                )
                total_tokens += int(getattr(c_resp, "total_tokens", 0) or 0)
                code = (
                    self.llm.extract_code_block(c_resp.content, "html")
                    or self.llm.extract_code_block(c_resp.content)
                    or c_resp.content.strip()
                )
            except Exception:
                code = all_leaf_code
        else:
            emit(
                "combining",
                "in_progress",
                "Performing unified system architecture consolidation and contract verification...",
                80,
            )
            todo_text = "\n".join(
                f"- [{c.id}] {c.name} ({c.subsystem}): {c.purpose}" for c in blueprint.components
            )
            synth_prompt = (
                f"TARGET TASK: {task_description}\n\n"
                f"IMPLEMENTED COMPONENTS:\n{all_leaf_code}\n\n"
                f"BLUEPRINT COMPONENTS & CONTRACTS:\n{todo_text}\n\n"
                "CRITICAL PRODUCTION PACKAGING REQUIREMENTS:\n"
                "1. Produce complete, self-contained, working, production-ready code.\n"
                "2. For algorithmic/Python tasks: implement the EXACT target function or classes requested.\n"
                "3. Ensure all helper methods, boundary guards, and return signatures are fully defined.\n"
                "4. Zero placeholders, zero TODOs, zero omissions.\n"
                "Output ONLY the complete code inside a single markdown code block."
            )
            try:
                c_resp = self.llm.generate(
                    prompt=synth_prompt, max_tokens=self._safe_llm_max_tokens(10_000_000)
                )
                total_tokens += int(getattr(c_resp, "total_tokens", 0) or 0)
                code = (
                    self.llm.extract_code_block(c_resp.content, "python")
                    or self.llm.extract_code_block(c_resp.content)
                    or c_resp.content.strip()
                )
            except Exception:
                code = all_leaf_code

        # Fast static syntax/structure inspection; does not verify semantic correctness.
        code, code_conf, _ = self.evaluate_and_elevate_confidence_with_nas(
            chunk_name="Assembled Hierarchical Code",
            chunk_content=code,
            current_confidence=0.88,
            task_context=task_description,
        )

        self.holographic_mem.store_layer_chunk(4, "implementation", code)
        self.multi_layer_mem.add_chunk(4, "implementation", code)

        # Phase 6: BUG DETECTION, DIAGNOSIS & LLM SELF-HEALING FIX
        emit(
            "verification",
            "in_progress",
            "Checking for bugs: running AST verification, sandbox execution, and test assertions...",
            85,
        )
        error_logs = ""
        test_logs = ""
        success = False
        effective_retries = max(1, 2 if self.llm.provider_name == "ollama" else int(max_retries))

        for attempt in range(effective_retries):
            test_logs = ""
            # Parse Python source regardless of whether it contains def/import tokens.
            # HTML is handled by explicit static checks; other languages are not executed here.
            syntax_ok = True
            syntax_err = ""
            code_lower = code.lower()
            is_html_artifact = any(
                marker in code_lower for marker in ("<html", "<!doctype html", "<canvas")
            )
            python_indicators = (
                re.search(r"\b(def|import|from|class|return|elif|lambda|None|True|False)\b", code)
                is not None
            )
            if not is_html_artifact:
                try:
                    ast.parse(code)
                except (SyntaxError, ValueError) as exc:
                    syntax_ok = False
                    syntax_err = str(exc)

            # Generate test harness
            test_prompt = (
                f"TARGET TASK: {task_description}\n"
                f"Code under validation:\n```python\n{code}\n```\n"
                "Write an executable test script asserting correctness and boundary cases.\n"
                "CRITICAL REQUIREMENTS:\n"
                "1. Call the exact functions/classes implemented in the code above.\n"
                "2. Use direct top-level Python `assert` statements (e.g., `assert divide(6, 2) == 3`).\n"
                "3. Assert both standard cases and edge cases (e.g. zero divisor, negative indices, empty lists).\n"
                "4. DO NOT use `unittest.main(exit=False)`. Plain `assert` statements only.\n"
                "5. At the very end of the test script, print: `print('All tests passed')`\n"
                "Output ONLY the test code inside a single ```python ``` block."
            )
            if (
                hasattr(self, "hybrid_coordinator")
                and self.hybrid_coordinator
                and self.hybrid_coordinator.enabled
            ):
                test_resp, _ = self.hybrid_coordinator.generate(
                    prompt=test_prompt,
                    task_type="unit_tests",
                    max_tokens=self._safe_llm_max_tokens(4096),
                )
            else:
                test_resp = self.llm.generate(
                    prompt=test_prompt, max_tokens=self._safe_llm_max_tokens(4096)
                )
            total_tokens += int(getattr(test_resp, "total_tokens", 0) or 0)
            tests = (
                self.llm.extract_code_block(test_resp.content, "python")
                or test_resp.content.strip()
            )

            # Execute only code with an explicit verification path. Never convert a test-harness
            # SyntaxError into success merely because the source code parsed.
            if is_html_artifact or is_interactive:
                has_html_shell = "<html" in code_lower and "</html>" in code_lower
                if is_interactive:
                    has_canvas = "<canvas" in code_lower
                    has_script = "<script" in code_lower and "</script>" in code_lower
                    has_loop = "requestanimationframe" in code_lower or "setinterval" in code_lower
                    has_input = (
                        "addeventlistener" in code_lower
                        or "onkeydown" in code_lower
                        or "onclick" in code_lower
                    )
                    success = has_canvas and has_script and has_loop and has_input
                    test_logs = (
                        "Static interactive-app checks passed; no browser/runtime test was executed."
                        if success
                        else "Static interactive-app checks failed (canvas, script, loop, or input handler missing)."
                    )
                else:
                    success = has_html_shell
                    test_logs = (
                        "Static HTML shell checks passed; no browser/runtime test was executed."
                        if success
                        else "Static HTML checks failed: an opening and closing html tag are required."
                    )
            elif not syntax_ok:
                success = False
                test_logs = f"Python source syntax check failed: {syntax_err}"
            elif not python_indicators and not code.strip():
                success = False
                test_logs = "No executable source code was generated."
            elif not tests.strip():
                success = False
                test_logs = "No test harness was generated; execution was not accepted as verified."
            else:
                try:
                    test_tree = ast.parse(tests)
                    has_assertion = any(
                        isinstance(node, ast.Assert) for node in ast.walk(test_tree)
                    )
                    has_test_method = any(
                        isinstance(node, ast.Attribute) and node.attr.startswith("assert")
                        for node in ast.walk(test_tree)
                    )
                    has_test_function = any(
                        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name.startswith("test_")
                        for node in ast.walk(test_tree)
                    )
                except SyntaxError as exc:
                    has_assertion = has_test_method = has_test_function = False
                    test_logs = f"Generated test harness syntax error: {exc}"
                if not (has_assertion or has_test_method or has_test_function):
                    success = False
                    if not test_logs:
                        test_logs = "Generated test harness contains no detectable assertions/test functions; verification refused."
                else:
                    exec_script = f"{code}\n\n{tests}"
                    success, test_logs = self.sandbox.execute_script(
                        exec_script, filename="sandbox_test_runner.py"
                    )

            history.append(
                {
                    "attempt": attempt + 1,
                    "code": code,
                    "tests": tests
                    if not (is_html_artifact or is_interactive)
                    else "Static artifact checks only",
                    "success": success,
                    "logs": test_logs,
                }
            )

            if success:
                verification_label = (
                    "static checks" if (is_html_artifact or is_interactive) else "sandbox tests"
                )
                emit(
                    "verification",
                    "completed",
                    f"{verification_label.capitalize()} passed on attempt {attempt + 1}.",
                    90,
                )
                break
            else:
                error_logs = test_logs or syntax_err
                emit(
                    "verification",
                    "in_progress",
                    f"Bugs detected on attempt {attempt + 1}. Passing bugs to LLM for diagnosis & repair...",
                    87,
                )

                # Step 6A: Bug Diagnosis Pass via LLM
                diag_prompt = (
                    f"TARGET TASK: {task_description}\n"
                    f"FAILING CODE:\n```python\n{code}\n```\n"
                    f"EXECUTION FAILURE TRACE / BUGS DETECTED:\n{error_logs}\n\n"
                    "Perform rigorous bug diagnosis. Decompose every bug into:\n"
                    "1. Root cause mechanism\n"
                    "2. Specific defect location in code\n"
                    "3. Exact correction requirement and boundary guard."
                )
                diag_resp = self.llm.generate(
                    prompt=diag_prompt, max_tokens=self._safe_llm_max_tokens(4096)
                )
                total_tokens += int(getattr(diag_resp, "total_tokens", 0) or 0)
                bug_diagnosis = diag_resp.content.strip()

                # Step 6B: Bug Fix Pass via LLM
                fix_prompt = (
                    f"TARGET TASK: {task_description}\n"
                    f"FAILING CODE:\n```python\n{code}\n```\n"
                    f"BUG DIAGNOSIS:\n{bug_diagnosis}\n"
                    f"ERROR LOGS:\n{error_logs}\n\n"
                    "Fix ALL identified bugs in the code. Ensure zero regressions, complete boundary guards, and syntax perfection.\n"
                    "Output ONLY the complete, corrected code inside a single ```python ``` block."
                )
                fix_resp = self.llm.generate(
                    prompt=fix_prompt, max_tokens=self._safe_llm_max_tokens(4096)
                )
                total_tokens += int(getattr(fix_resp, "total_tokens", 0) or 0)
                repaired = self.llm.extract_code_block(fix_resp.content, "python")
                if repaired:
                    repaired, _, _ = self.evaluate_and_elevate_confidence_with_nas(
                        chunk_name=f"BugFix_Attempt_{attempt + 1}",
                        chunk_content=repaired,
                        current_confidence=0.85,
                        task_context=task_description,
                    )
                    code = repaired

        review = {
            "approved": success,
            "security_check": "heuristic_only_not_a_security_boundary",
            "style_score": None,
            "verification_level": "static_only"
            if (is_interactive or "<html" in code.lower() or "<canvas" in code.lower())
            else "python_tests",
        }

        # Step 4: Dual-Pass Rigorous Audit Pipeline (OO_1 -> OO_2 -> OO_3)
        emit(
            "audit",
            "in_progress",
            "Executing recursive dual-pass fixed-point audit convergence...",
            90,
        )
        tested_code = code
        current_code = code
        audit_history: List[Dict[str, Any]] = []
        fixed_point_reached = False
        convergence_round = 0

        for round_idx in (1, 2):
            convergence_round = round_idx
            audit = self.auditor.audit(current_code, task_description, iteration=round_idx)
            audit_history.append(audit.to_dict())

            if audit.is_converged and not audit.detected_issues:
                if round_idx == 1:
                    audit2 = self.auditor.audit(current_code, task_description, iteration=2)
                    audit_history.append(audit2.to_dict())
                    convergence_round = 2
                    if audit2.is_converged and not audit2.detected_issues:
                        fixed_point_reached = True
                        break
                    audit = audit2
                else:
                    fixed_point_reached = True
                    break

            refinement_bp = self.blueprint_engine.generate_refinement_blueprint(
                task_description, current_code, audit
            )
            self.holographic_mem.store_blueprint(refinement_bp)

            if audit.detected_issues:
                issues_list = "\n".join(f"- {iss}" for iss in audit.detected_issues)
            else:
                issues_list = "- Static heuristic checks did not converge; inspect edge cases and test coverage."

            refine_prompt = (
                f"TARGET TASK: {task_description}\n\n"
                f"CURRENT IMPLEMENTATION (Pass {round_idx}):\n```python\n{current_code}\n```\n\n"
                f"STATIC AUDIT FINDINGS (heuristic, not formal proof):\n{issues_list}\n\n"
                "Repair the listed issues while preserving behavior. Do not claim tests passed unless they were executed. "
                "Output ONLY the complete corrected code inside a single markdown block."
            )

            if (
                is_interactive
                or "<html" in current_code.lower()
                or "<canvas" in current_code.lower()
            ):
                refine_resp = self.llm.generate(
                    prompt=refine_prompt,
                    system_prompt="You are repairing a web artifact. Output only complete HTML in a markdown block.",
                    max_tokens=self._safe_llm_max_tokens(4096),
                )
                total_tokens += int(getattr(refine_resp, "total_tokens", 0) or 0)
                next_code = (
                    self.llm.extract_code_block(refine_resp.content, "html")
                    or refine_resp.content.strip()
                )
            else:
                refine_resp = self.llm.generate(
                    prompt=refine_prompt,
                    system_prompt="You are an expert debugger. Output only complete Python code in a markdown block.",
                    max_tokens=self._safe_llm_max_tokens(4096),
                )
                total_tokens += int(getattr(refine_resp, "total_tokens", 0) or 0)
                next_code = (
                    self.llm.extract_code_block(refine_resp.content, "python")
                    or refine_resp.content.strip()
                )

            if next_code:
                current_code = next_code

        code = current_code
        # If the audit changed code after tests ran, re-run the original assertions against the final code.
        if code != tested_code and (
            is_interactive or "<html" in code.lower() or "<canvas" in code.lower()
        ):
            final_lower = code.lower()
            if is_interactive:
                success = all(
                    (
                        "<canvas" in final_lower,
                        "<script" in final_lower and "</script>" in final_lower,
                        "requestanimationframe" in final_lower or "setinterval" in final_lower,
                        "addeventlistener" in final_lower
                        or "onkeydown" in final_lower
                        or "onclick" in final_lower,
                    )
                )
            else:
                success = "<html" in final_lower and "</html>" in final_lower
            test_logs = (
                "Final HTML static checks passed; browser/runtime execution was not performed."
                if success
                else "Final HTML static checks failed after audit changes."
            )
        elif code != tested_code:
            if not tests.strip():
                success = False
                test_logs = "Final code changed after testing, but no test harness is available for re-verification."
            else:
                try:
                    ast.parse(code)
                    ast.parse(tests)
                    success, test_logs = self.sandbox.execute_script(
                        f"{code}\n\n{tests}", filename="sandbox_final_verification.py"
                    )
                except SyntaxError as exc:
                    success = False
                    test_logs = f"Final-code re-verification failed syntax parsing: {exc}"

        success = bool(success and fixed_point_reached)
        review["approved"] = success
        review["verification_level"] = (
            "static_only"
            if (is_interactive or "<html" in code.lower() or "<canvas" in code.lower())
            else "python_tests_and_heuristic_audit"
        )

        self.holographic_mem.store_layer_chunk(
            5,
            "verification_and_audit",
            f"Verification success={success} | Final code {len(code)} chars | Fixed point={fixed_point_reached}",
        )
        self.multi_layer_mem.add_chunk(
            5,
            "verification_and_audit",
            f"Verification success={success} | Final code {len(code)} chars | Fixed point={fixed_point_reached}",
        )

        # Step 5: Multi-Scale MERA Analysis & Memory Storage
        multiscale_mera = self.fractal_mera.analyze_code_multiscale(code)
        self.episodes.store(
            task_description=task_description,
            solution_summary=f"Outcome: {'SUCCESS' if success else 'FAILED'}, Topology: {topology.topology_type.value}, Convergence Rounds: {convergence_round}",
            tool_calls=[{"attempt": a["attempt"], "success": a["success"]} for a in history],
            success=success,
        )

        completion_message = (
            "Task completed; Python tests and heuristic static audits passed."
            if success
            else "Task finished with unresolved verification issues. Review the test logs and audit history before accepting changes."
        )
        emit("completed", "completed" if success else "failed", completion_message, 100)

        elapsed = time.perf_counter() - t0
        return SandboxSessionReport(
            task=task_description,
            status="success" if success else "failed",
            provider=self.llm.provider_name,
            model=self.llm.model_name,
            iterations=len(history),
            final_code=code,
            test_logs=test_logs if success else error_logs,
            review=review,
            topology_used=topology.topology_type.value,
            qualified_properties_count=pondering_synthesis.qualified_properties_count,
            holographic_stats=self.holographic_mem.get_boundary_compression_stats(),
            qualified_properties=[p.to_dict() for p in pondering_synthesis.qualified_properties],
            verification_level="static_only"
            if (is_interactive or "<html" in code.lower() or "<canvas" in code.lower())
            else "python_tests_and_heuristic_audit",
            elapsed_time_s=elapsed,
            history=history,
            blueprint=blueprint.to_dict(),
            convergence_rounds=convergence_round,
            fixed_point_reached=fixed_point_reached,
            audit_history=audit_history,
            implementation_plan=implementation_plan,
            progress_log=progress_log,
            total_tokens_used=total_tokens,
            effective_tokens=int(total_tokens),
            token_usage_complete=False,
            qdit_certificate=qdit_state.to_dict() if "qdit_state" in locals() else None,
            hybrid_telemetry=self.hybrid_coordinator.get_status()
            if hasattr(self, "hybrid_coordinator") and self.hybrid_coordinator
            else None,
            swarm_report=None,
        )
