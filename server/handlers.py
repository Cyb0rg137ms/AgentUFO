"""
handlers.py
===========
Backend controller and API request handlers for Agent-IDE Sandbox Web UI.
Handles model execution, dynamic provider switching, TypeSafe AI Jev fast decisions,
custom MCP Server Studio development, projects, artifacts, and persistent chats.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Load .env file from project root if present
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    try:
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())
    except Exception:
        pass

from agent_core.agentufo_swarm import (
    AgentUFOCoordinator,
    AgentUFOMode,
    BriefMessage,
    SeatTokenPolicy,
)
from agent_core.budget_guard import HardBudgetGuard
from agent_core.capability_prober import EmpiricalCapabilityProber
from agent_core.llm import (
    LLMRegistry,
    OllamaProvider,
)
from agent_core.llm.ollama_launcher import OllamaAutoLauncher
from agent_core.mcp_studio import (
    MCPSandboxTester,
    MCPServerDefinition,
    MCPServerScaffolder,
    MCPToolDefinition,
    MCPToolParameter,
    get_default_mcp_presets,
)
from agent_core.operator_interface import (
    JevDecisionEngine,
    ProgressEvent,
    SandboxOperatorInterface,
    TaskIntent,
)
from agent_core.review_gate import ReviewGate
from agent_core.workspace_manager import WorkspaceManager
from server.starter_artifacts import get_starter_artifacts


def _update_env_file(env_path: Path, key: str, value: str) -> None:
    """Upserts a KEY=VALUE line in the .env file atomically."""
    existing_lines: List[str] = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = False
    new_lines = []
    for line in existing_lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


class ChatSessionManager:
    """Stateful manager for user chat sessions, active providers, and settings."""

    def __init__(self) -> None:
        # Auto-detect the best available LLM provider at startup:
        # Precedence: reachable Ollama → configured cloud provider keys → explicit unconfigured state
        _detected = LLMRegistry.auto_detect()
        self.provider_name = _detected.provider_name
        self.model_name = _detected.model_name
        self.enable_logos = True
        self.temperature = 0.2
        self.max_retries = 3
        self.custom_endpoint: Optional[str] = None
        self.history: List[Dict[str, Any]] = []
        self._status_cache: Optional[Dict[str, Any]] = None
        self._status_cache_at = 0.0

        # TypeSafe AI Jev Fast Decision Engine
        self.jev_engine = JevDecisionEngine()

        # Ollama Auto-Launcher & Local AI Studio
        self.ollama_launcher = OllamaAutoLauncher(
            host=self.custom_endpoint or "http://127.0.0.1:11434"
        )

        # MCP Tester
        self.mcp_tester = MCPSandboxTester()

        # Artifacts repository
        self.artifacts: List[Dict[str, Any]] = []

        # Saved Chat Sessions (Starts empty, no hardcoded data)
        self.saved_chats: Dict[str, Dict[str, Any]] = {}
        self.current_chat_id: Optional[str] = None

        # Pinned Projects (Starts empty)
        self.projects: List[Dict[str, Any]] = []

        # Save Tokens Feature (Hybrid Tiering)
        self.enable_hybrid = True

        # BridgeMind Swarm settings
        self.swarm_mode: Optional[str] = None
        self.swarm_agents: int = 4
        self.swarm_policy: str = "all"

        # Initialize operator interface with the detected provider
        _init_kwargs: Dict[str, Any] = {}
        if self.provider_name == "ollama" and self.custom_endpoint:
            _init_kwargs["host"] = self.custom_endpoint
        self.operator = SandboxOperatorInterface(
            provider_name=self.provider_name,
            model_name=self.model_name,
            enable_logos=self.enable_logos,
            enable_hybrid=self.enable_hybrid,
            **_init_kwargs,
        )

        # AgentUFO Architecture: Real workspace locking, Review gate, Hard budget guard, Empirical capability prober
        self.workspace_mgr = WorkspaceManager(workspace_root=Path(os.getcwd()).resolve())
        self.budget_guard = HardBudgetGuard()
        self.capability_prober = EmpiricalCapabilityProber(workspace_root=self.workspace_mgr.root)
        self.review_gate = ReviewGate()
        self.agentufo_mode = "crew_pipeline"
        self.agentufo_seats = 3
        self.agentufo_policy = "all"
        self.agentufo_coordinator = AgentUFOCoordinator(
            cloud_llm=self.operator.llm,
            local_llm=getattr(self.operator.hybrid_coordinator, "local_llm", None)
            if hasattr(self.operator, "hybrid_coordinator")
            else None,
            workspace=self.workspace_mgr,
            num_seats=self.agentufo_seats,
            mode=AgentUFOMode.CREW_PIPELINE,
            token_policy=SeatTokenPolicy.ALL_TIERED,
            budget_guard=self.budget_guard,
            review_gate=self.review_gate,
            capability_prober=self.capability_prober,
        )

        # The fleet starts empty. Never claim unconfigured provider/model assignments are live.
        self.multi_llm_fleet: List[Dict[str, Any]] = []
        self.seat_model_bindings: Dict[str, Dict[str, str]] = {}

        # Seed initial artifacts from clean static starter templates
        self._seed_starter_artifacts()

        # Background daemon for empirical model capability probing
        try:
            _probe_thread = threading.Thread(
                target=self.capability_prober.auto_probe_daemon,
                kwargs={"host": self.custom_endpoint or "http://127.0.0.1:11434"},
                daemon=True,
                name="AgentUFOAutoProbeDaemon",
            )
            _probe_thread.start()
        except Exception:
            pass

    def _sync_coordinator_provider(self) -> None:
        """Keep the crew coordinator bound to the current operator provider objects."""
        coordinator = getattr(self, "agentufo_coordinator", None)
        operator = getattr(self, "operator", None)
        if coordinator is None or operator is None:
            return
        coordinator.cloud_llm = operator.llm
        coordinator.local_llm = getattr(
            getattr(operator, "hybrid_coordinator", None), "local_llm", None
        )

    def _seed_starter_artifacts(self) -> None:
        """Seeds demo artifacts that showcase the sandbox capabilities."""
        self.artifacts = get_starter_artifacts()

    def get_status(self) -> Dict[str, Any]:
        """Returns a short-lived cached health snapshot to avoid repeated provider/network probes."""
        now_mono = time.monotonic()
        if self._status_cache is not None and now_mono - self._status_cache_at < 4.0:
            return dict(self._status_cache)
        ollama = OllamaProvider(host=self.custom_endpoint or "http://127.0.0.1:11434")
        ollama_online = False
        ollama_models: List[str] = []
        try:
            ollama_online = ollama.is_available()
            if ollama_online:
                ollama_models = ollama.list_local_models()
        except Exception:
            pass

        keys = {
            "openai": bool(os.getenv("OPENAI_API_KEY")),
            "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
            "grok": bool(os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")),
            "gemini": bool(os.getenv("GEMINI_API_KEY")),
        }

        result = {
            "active_provider": self.provider_name,
            "active_model": self.model_name,
            "provider_configured": self.provider_name != "unconfigured",
            "provider_available": bool(getattr(self.operator.llm, "is_available", lambda: False)()),
            "enable_logos": self.enable_logos,
            "enable_hybrid": self.enable_hybrid,
            "swarm_mode": self.swarm_mode,
            "swarm_agents": self.swarm_agents,
            "swarm_policy": self.swarm_policy,
            "hybrid_status": self.get_hybrid_status(),
            "swarm_status": self.get_swarm_status(),
            "ollama": {
                "online": ollama_online,
                "host": ollama.host,
                "installed_models": ollama_models,
            },
            "configured_api_keys": keys,
            "holographic_memory": self.operator.holographic_mem.get_boundary_compression_stats(),
            "conversation_turns": len(self.history),
            "artifacts_count": len(self.artifacts),
            "projects_count": len(self.projects),
            "saved_chats_count": len(self.saved_chats),
        }
        self._status_cache = result
        self._status_cache_at = time.monotonic()
        return dict(result)

    def get_hybrid_status(self) -> Dict[str, Any]:
        """Returns live telemetry for the Save Tokens (Hybrid Cloud + Local) feature."""
        res: Dict[str, Any] = {
            "enabled": self.enable_hybrid,
            "hybrid_tiering_enabled": self.enable_hybrid,
            "local_ready": False,
        }
        if hasattr(self.operator, "hybrid_coordinator") and self.operator.hybrid_coordinator:
            status = self.operator.hybrid_coordinator.get_status()
            res.update(status)
            res["hybrid_tiering_enabled"] = status.get("enabled", self.enable_hybrid)
        return res

    def configure_hybrid(
        self, settings: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        """Configures the Save Tokens (Hybrid Cloud + Local) feature."""
        cfg = dict(settings or {})
        cfg.update(kwargs)
        if "enabled" in cfg:
            self.enable_hybrid = bool(cfg["enabled"])
            if hasattr(self.operator, "hybrid_coordinator") and self.operator.hybrid_coordinator:
                self.operator.hybrid_coordinator.enabled = self.enable_hybrid
        return self.get_hybrid_status()

    def _apply_seat_models(self) -> None:
        """Applies configured seat model bindings to the crew seats."""
        for s in self.agentufo_coordinator.seats:
            if s.seat_id in self.seat_model_bindings:
                b = self.seat_model_bindings[s.seat_id]
                s.assigned_provider = b.get("provider")
                s.assigned_model = b.get("model")

    def get_agentufo_status(self) -> Dict[str, Any]:
        """Returns AgentUFO crew status, active locks, budget, multi-LLM fleet, and prober cache."""
        active_locks = self.workspace_mgr.list_active_locks()
        budget_summary = self.budget_guard.get_summary()
        probe_cache = (
            self.capability_prober.get_cached_profiles()
            if hasattr(self.capability_prober, "get_cached_profiles")
            else self.capability_prober._profiles
        )
        mailbox = [m.to_dict() for m in self.agentufo_coordinator.mailbox]
        seats = [s.to_dict() for s in self.agentufo_coordinator.seats]
        return {
            "name": "AgentUFO",
            "mode": self.agentufo_mode,
            "num_seats": len(seats),
            "num_agents": len(seats),
            "swarm_agents": len(seats),
            "token_policy": self.agentufo_policy,
            "token_saving_policy": self.agentufo_policy,
            "seats": seats,
            "multi_llm_fleet": self.multi_llm_fleet,
            "active_fleet_count": sum(1 for m in self.multi_llm_fleet if m.get("active")),
            "seat_model_bindings": self.seat_model_bindings,
            "locks": [l.to_dict() for l in active_locks],
            "mailbox_count": len(mailbox),
            "mailbox": mailbox[-25:],
            "budget": budget_summary,
            "probe_cache_count": len(probe_cache),
            "workspace_root": str(self.workspace_mgr.root),
        }

    def configure_agentufo(
        self, settings: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        """Configures AgentUFO crew settings (mode, seats, policy, budget caps, multi-LLM fleet)."""
        cfg = dict(settings or {})
        cfg.update(kwargs)
        if "mode" in cfg:
            m = cfg["mode"]
            if m in ("crew_pipeline", "split_work", "consensus", "competitive_consensus"):
                self.agentufo_mode = m
                mode_val = (
                    AgentUFOMode.SPLIT_WORK
                    if m == "split_work"
                    else (
                        AgentUFOMode.COMPETITIVE_CONSENSUS
                        if "consensus" in m
                        else AgentUFOMode.CREW_PIPELINE
                    )
                )
                self.agentufo_coordinator.mode = mode_val
        if "num_seats" in cfg or "num_agents" in cfg or "swarm_agents" in cfg:
            n = cfg.get("num_seats") or cfg.get("num_agents") or cfg.get("swarm_agents")
            try:
                self.agentufo_seats = max(2, min(16, int(n)))
                self.agentufo_coordinator.num_seats = self.agentufo_seats
                self.agentufo_coordinator._init_crew_seats()
            except Exception:
                pass
        if "token_policy" in cfg or "token_saving_policy" in cfg:
            p = cfg.get("token_policy") or cfg.get("token_saving_policy")
            if p in ("all", "leader_only", "cloud_only"):
                self.agentufo_policy = p
                self.agentufo_coordinator.token_policy = SeatTokenPolicy(p)
        if "budget" in cfg and isinstance(cfg["budget"], dict):
            b = cfg["budget"]
            if "total_spend_limit_usd" in b or "max_usd_cap" in b:
                self.budget_guard.config.max_usd_cap = float(
                    b.get("total_spend_limit_usd") or b.get("max_usd_cap")
                )
            if "max_total_tokens" in b or "total_token_cap" in b:
                self.budget_guard.config.total_token_cap = int(
                    b.get("max_total_tokens") or b.get("total_token_cap")
                )
            if "per_seat_token_cap" in b:
                self.budget_guard.config.per_seat_token_cap = int(b["per_seat_token_cap"])
            if "planner_token_cap" in b:
                self.budget_guard.config.planner_token_cap = int(b["planner_token_cap"])

        # Update Multi-LLM Fleet Matrix
        if "multi_llm_fleet" in cfg and isinstance(cfg["multi_llm_fleet"], list):
            self.multi_llm_fleet = cfg["multi_llm_fleet"]

        # Update per-seat model bindings
        if "seat_models" in cfg and isinstance(cfg["seat_models"], dict):
            self.seat_model_bindings.update(cfg["seat_models"])
        elif "seat_model_bindings" in cfg and isinstance(cfg["seat_model_bindings"], dict):
            self.seat_model_bindings.update(cfg["seat_model_bindings"])

        self._apply_seat_models()

        self.swarm_mode = self.agentufo_mode
        self.swarm_agents = self.agentufo_seats
        self.swarm_policy = self.agentufo_policy

        return self.get_agentufo_status()

    def get_agentufo_cockpit(self) -> Dict[str, Any]:
        """Returns live multi-seat token generation & thought telemetry without mutating seat configuration."""
        telemetry = self.agentufo_coordinator.get_cockpit_telemetry()
        telemetry["multi_llm_fleet"] = self.multi_llm_fleet
        telemetry["seat_model_bindings"] = self.seat_model_bindings
        telemetry["budget"] = self.budget_guard.get_summary()
        return telemetry

    def execute_seat_action(
        self, seat_id: str, action: str, context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Executes a live steering action (pause, resume, stop, edit/inject) on a seat."""
        success = False
        if action == "pause":
            success = self.agentufo_coordinator.pause_seat(seat_id)
        elif action == "resume":
            success = self.agentufo_coordinator.resume_seat(seat_id)
        elif action == "stop":
            success = self.agentufo_coordinator.stop_seat(seat_id)
        elif action in ("edit", "inject"):
            success = self.agentufo_coordinator.inject_context(seat_id, context or "")
        return {
            "success": success,
            "seat_id": seat_id,
            "action": action,
            "cockpit": self.get_agentufo_cockpit(),
        }

    def get_agentufo_mailbox(self) -> Dict[str, Any]:
        """Returns all messages in the durable AgentUFO mailbox."""
        return {
            "count": len(self.agentufo_coordinator.mailbox),
            "messages": [m.to_dict() for m in self.agentufo_coordinator.mailbox],
        }

    def get_agentufo_locks(self) -> Dict[str, Any]:
        """Returns all active file locks across the real workspace."""
        locks = self.workspace_mgr.list_active_locks()
        return {
            "count": len(locks),
            "locks": [l.to_dict() for l in locks],
            "lock_file": str(self.workspace_mgr.locks_file),
            "workspace_root": str(self.workspace_mgr.root),
        }

    def break_agentufo_lock(
        self, target_path: str, breaker_id: str = "master_admin", reason: str = ""
    ) -> Dict[str, Any]:
        """Administrative override breaking a stale or crash-abandoned lock on disk."""
        success = self.workspace_mgr.break_lock(target_path, breaker_id=breaker_id, reason=reason)
        active_locks = self.workspace_mgr.list_active_locks()
        return {
            "status": "success" if success else "failed",
            "path": target_path,
            "broken_by": breaker_id,
            "active_locks": [l.to_dict() for l in active_locks],
        }

    def claim_agentufo_paths(
        self,
        seat_id: str,
        paths: List[str],
        seat_role: str = "builder",
        lease_seconds: float = 600.0,
    ) -> Dict[str, Any]:
        """Claims multiple paths for a seat with strict on-disk mutual exclusion."""
        result = self.workspace_mgr.claim_paths(
            seat_id, paths, seat_role=seat_role, lease_seconds=lease_seconds
        )
        return result

    def handoff_agentufo_brief(
        self,
        from_seat: str,
        to_seat: str,
        title: str,
        body: str,
        brief_type: str = "handoff",
    ) -> Dict[str, Any]:
        """Submits an inter-seat handoff brief to the durable AgentUFO mailbox."""
        brief = BriefMessage(
            message_id=f"brief-{uuid.uuid4().hex[:8]}",
            from_seat=from_seat,
            to_seat=to_seat,
            msg_type=brief_type,
            title=title,
            payload={"body": body, "brief_type": brief_type},
            timestamp=time.time(),
        )
        self.agentufo_coordinator.mailbox.append(brief)
        # Shared workspace/task changes are injected into each seat's durable context,
        # not merely left as a mailbox message that active agents may never read.
        if to_seat == "all":
            shared_context = f"{title}\n{body}"[:12000]
            for seat in self.agentufo_coordinator.seats:
                self.agentufo_coordinator.inject_context(seat.seat_id, shared_context)
        else:
            self.agentufo_coordinator.inject_context(to_seat, f"{title}\n{body}"[:12000])
        return {
            "status": "delivered",
            "brief": brief.to_dict(),
            "mailbox_count": len(self.agentufo_coordinator.mailbox),
        }

    def get_agentufo_prober_status(self) -> Dict[str, Any]:
        """Returns empirical probe benchmarks for all local/remote models."""
        profiles = self.capability_prober.get_cached_profiles()
        return {
            "count": len(profiles),
            "profiles": {k: v.to_dict() for k, v in profiles.items()},
        }

    def rank_agentufo_models(self, candidate_models: Optional[List[str]] = None) -> Dict[str, Any]:
        """Ranks models by empirical benchmark scores instead of a static name table."""
        ranked = self.capability_prober.rank_models(candidate_models=candidate_models)
        return {
            "ranked_models": [r.to_dict() for r in ranked],
            "count": len(ranked),
        }

    def run_agentufo_mission(
        self,
        task: str,
        progress_callback: Optional[Callable[[str, int, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Executes a full mission via the AgentUFO Crew."""
        self.agentufo_coordinator.cloud_llm = self.operator.llm
        if hasattr(self.operator, "hybrid_coordinator") and self.operator.hybrid_coordinator:
            self.agentufo_coordinator.local_llm = self.operator.hybrid_coordinator.local_llm

        report = self.agentufo_coordinator.run_crew(task, progress_callback=progress_callback)
        return report.to_dict()

    # Backward compatibility aliases for Swarm API
    get_swarm_status = get_agentufo_status
    configure_swarm = configure_agentufo

    def get_models(self) -> Dict[str, Any]:
        """Returns catalog of local and cloud models."""
        ollama = OllamaProvider(host=self.custom_endpoint or "http://127.0.0.1:11434")
        local_models = []
        try:
            if ollama.is_available():
                local_models = ollama.list_local_models()
        except Exception:
            pass

        return {
            "local_models": local_models,
            "presets": {
                "ollama": local_models
                if local_models
                else ["qwen2.5-coder:7b", "deepseek-r1:14b", "llama3.2:3b"],
                "openai": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
                "anthropic": [
                    "claude-3-7-sonnet-20250219",
                    "claude-3-5-sonnet-20241022",
                    "claude-3-opus-20240229",
                ],
                "claude": [
                    "claude-3-7-sonnet-20250219",
                    "claude-3-5-sonnet-20241022",
                    "claude-3-opus-20240229",
                ],
                "grok": ["grok-2-latest", "grok-beta"],
                "gemini": [
                    "gemini-2.0-flash",
                    "gemini-1.5-pro",
                    "gemini-1.5-flash",
                    "gemini-2.0-flash-lite",
                ],
                "deepseek": [
                    "deepseek-chat",
                    "deepseek-reasoner",
                    "deepseek-flash",
                    "deepseek-v4-pro",
                ],
                "kimi": ["kimi-k2", "moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
                "minimax": ["MiniMax-Text-01", "abab6.5s-chat", "abab6.5-chat"],
                "qwen": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen2.5-coder-32b-instruct"],
                "local": ["local-model"],
            },
        }

    def update_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Dynamically switches providers, keys, and model parameters at runtime."""
        requested_provider = str(settings.get("provider") or self.provider_name).lower().strip()
        if requested_provider not in LLMRegistry._PROVIDERS:
            return {
                "status": "error",
                "error": f"Unknown provider '{requested_provider}'. Choose from: {', '.join(sorted(LLMRegistry._PROVIDERS))}",
            }
        if "provider" in settings and settings["provider"]:
            self.provider_name = requested_provider
        if "model" in settings and settings["model"]:
            self.model_name = settings["model"].strip()
        if "enable_logos" in settings:
            self.enable_logos = bool(settings["enable_logos"])
        if "temperature" in settings:
            self.temperature = float(settings["temperature"])
        if "max_retries" in settings:
            self.max_retries = int(settings["max_retries"])
        if "custom_endpoint" in settings:
            self.custom_endpoint = settings["custom_endpoint"]

        # Update environment keys dynamically if passed
        _ENV_VAR_MAP = {
            "openai": "OPENAI_API_KEY",
            "local": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "grok": "XAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "kimi": "MOONSHOT_API_KEY",
            "moonshot": "MOONSHOT_API_KEY",
            "minimax": "MINIMAX_API_KEY",
            "qwen": "DASHSCOPE_API_KEY",
            "dashscope": "DASHSCOPE_API_KEY",
        }
        if "api_key" in settings and settings["api_key"]:
            key = settings["api_key"].strip()
            env_var = _ENV_VAR_MAP.get(self.provider_name)
            if env_var:
                os.environ[env_var] = key
                # Persist to .env
                try:
                    _update_env_file(_env_file, env_var, key)
                except Exception:
                    pass

        # Re-initialize operator with the new provider
        kwargs: Dict[str, Any] = {}
        if self.custom_endpoint and self.provider_name == "ollama":
            kwargs["host"] = self.custom_endpoint
        elif self.custom_endpoint and self.provider_name in ["openai", "local"]:
            kwargs["base_url"] = self.custom_endpoint

        self.operator = SandboxOperatorInterface(
            provider_name=self.provider_name,
            model_name=self.model_name,
            enable_logos=self.enable_logos,
            enable_hybrid=self.enable_hybrid,
            **kwargs,
        )
        # Rebind the crew to the active provider; otherwise the UI and crew can silently diverge.
        self._sync_coordinator_provider()
        self._status_cache = None
        self._status_cache_at = 0.0
        return self.get_status()

    def process_chat(
        self,
        prompt: str,
        progress_callback: Optional[Callable[[ProgressEvent], None]] = None,
        enable_hybrid: Optional[bool] = None,
        swarm_mode: Optional[str] = None,
        swarm_agents: Optional[int] = None,
        swarm_policy: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Processes a user prompt through TypeSafe AI Jev Fast Decision Pipeline,
        LOGOS cognitive architecture, and isolated sandbox execution.
        """
        p_clean = prompt.strip()
        t0 = time.perf_counter()

        # Step 1: TypeSafe AI Jev Fast Parallel Decision Pass (sub-15ms)
        jev_report = self.jev_engine.evaluate(p_clean)

        # Immediate Safety Boundary Gate
        if not jev_report.is_safe.decision:
            return {
                "type": "conversational",
                "content": "⚠️ **Security Violation Detected**: The prompt contains prohibited patterns (unconstrained shell escaping, destructive filesystem calls). Execution aborted by Safety Guard.",
                "code": None,
                "test_logs": None,
                "status": "error",
                "review": {"security_check": "failed", "style_score": 0.0},
                "thinking": None,
                "jev": jev_report.to_dict(),
                "provider": self.provider_name,
                "model": self.model_name,
                "elapsed_seconds": round(time.perf_counter() - t0, 3),
            }

        # Step 2: Determine if Sandbox Execution or Conversational
        is_explanation = p_clean.lower().startswith(
            ("explain", "what is", "what are", "why", "describe", "overview", "tell me about")
        ) and not any(
            k in p_clean.lower()
            for k in [
                "write code",
                "implement",
                "def ",
                "class ",
                "write a",
                "write function",
                "build",
                "create",
                "make",
            ]
        )
        if is_explanation:
            is_coding = False
        else:
            is_coding = (
                jev_report.requires_sandbox.decision
                or jev_report.intent.value in [TaskIntent.CODING, TaskIntent.MCP_DEV]
                or any(
                    k in p_clean.lower()
                    for k in [
                        "write",
                        "code",
                        "implement",
                        "def ",
                        "class ",
                        "fix",
                        "solve",
                        "divide",
                        "algorithm",
                        "script",
                        "program",
                        "compile",
                        "benchmark",
                        "build",
                        "create a",
                        "make a",
                        "develop",
                        "design",
                    ]
                )
            )

        if is_coding:
            # Build conversation context (last 3 turns)
            context = self._build_context(3)
            hyb = self.enable_hybrid if enable_hybrid is None else bool(enable_hybrid)
            sm = self.swarm_mode if swarm_mode is None else swarm_mode
            sa = self.swarm_agents if swarm_agents is None else swarm_agents
            sp = self.swarm_policy if swarm_policy is None else swarm_policy

            # Run through optimized sandbox operator interface
            try:
                report = self.operator.solve_task(
                    p_clean,
                    max_retries=self.max_retries,
                    progress_callback=progress_callback,
                    conversation_history=context,
                    enable_hybrid=hyb,
                    swarm_mode=sm,
                    swarm_agents=sa,
                    swarm_policy=sp,
                )
            except (ConnectionError, OSError, Exception) as _llm_err:
                err_result = self._llm_error_result(p_clean, t0, _llm_err)
                self._append_history(p_clean, err_result)
                self._auto_save_chat()
                return err_result

            # Fast MERA coarse-graining on the resulting code
            multiscale = self.operator.fractal_mera.analyze_code_multiscale(report.final_code)

            thinking_data = {
                "topology": report.topology_used,
                "summary": f"Workflow used topology '{report.topology_used}'. Confidence values are uncalibrated heuristic scores; see verification_level and audit history for actual checks.",
                "subquestions": [
                    f"Architecture & State for: '{p_clean[:60]}...'",
                    "Input boundaries and resource limits",
                    "Runtime verification status and remaining risks",
                ],
                "qualified_properties": report.qualified_properties,
                "mera_summary": self.operator.fractal_mera.coarse_grain_summary(multiscale),
                "holographic_stats": report.holographic_stats,
                "blueprint": report.blueprint,
                "convergence_rounds": report.convergence_rounds,
                "fixed_point_reached": report.fixed_point_reached,
                "audit_history": report.audit_history,
                "effective_tokens": int(getattr(report, "effective_tokens", 0) or 0),
                "total_tokens_used": int(getattr(report, "total_tokens_used", 0) or 0),
                "token_usage_complete": bool(getattr(report, "token_usage_complete", False)),
                "verification_level": getattr(report, "verification_level", "not_verified"),
                "implementation_plan": getattr(report, "implementation_plan", None),
                "progress_log": [
                    e.to_dict() if hasattr(e, "to_dict") else e
                    for e in getattr(report, "progress_log", [])
                ],
                "hybrid_telemetry": getattr(report, "hybrid_telemetry", None),
                "swarm_report": getattr(report, "swarm_report", None),
            }

            is_app = any(
                k in report.final_code.lower() for k in ["<html", "<canvas", "<!doctype html"]
            )
            if is_app:
                verification_note = (
                    "Static structure checks passed; this artifact was not executed in a real browser."
                    if report.review.get("verification_level") == "static_only"
                    else "See the verification report before using this artifact."
                )
                reply_text = (
                    f"Generated **{p_clean}** using topology `{report.topology_used}`.\n\n"
                    f"**Verification level:** {verification_note}\n\n"
                    f"Review the source and use **🎮 Live Play / Preview** to inspect the artifact."
                )
            else:
                if report.status == "success":
                    status_line = "- **Status**: Passed the reported verification checks"
                    test_summary = report.test_logs[:500] or "No test log was returned."
                else:
                    status_line = "- **Status**: ⚠️ Verification Discrepancy (Requires Review)"
                    test_summary = f"Sandbox test output:\n```\n{report.test_logs[:200]}\n```"

                style_score = report.review.get("style_score")
                style_text = (
                    f"{style_score}/10" if isinstance(style_score, (int, float)) else "Not measured"
                )
                reply_text = (
                    f"Implementation result for **{p_clean}** using topology `{report.topology_used}`.\n\n"
                    f"{status_line}\n"
                    f"- **Verification**: {test_summary}\n"
                    f"- **Security analysis**: `{report.review.get('security_check', 'not measured')}`\n"
                    f"- **Code style score**: `{style_text}`\n"
                )

            # Record artifact
            artifact_id = f"art-{uuid.uuid4().hex[:8]}"
            self.artifacts.append(
                {
                    "id": artifact_id,
                    "title": f"Artifact: {p_clean[:40]}",
                    "type": "html_app" if is_app else "python_script",
                    "language": "html" if is_app else "python",
                    "code": report.final_code,
                    "created_at": time.time(),
                    "status": report.status,
                }
            )

            result = {
                "type": "code_execution",
                "content": reply_text,
                "code": report.final_code,
                "test_logs": report.test_logs,
                "status": report.status,
                "review": report.review,
                "thinking": thinking_data,
                "artifact_id": artifact_id,
                "provider": report.provider,
                "model": report.model,
                "elapsed_seconds": round(time.perf_counter() - t0, 2),
                "effective_tokens": int(getattr(report, "effective_tokens", 0) or 0),
                "total_tokens_used": int(getattr(report, "total_tokens_used", 0) or 0),
                "token_usage_complete": bool(getattr(report, "token_usage_complete", False)),
                "verification_level": getattr(report, "verification_level", "not_verified"),
                "implementation_plan": getattr(report, "implementation_plan", None),
                "progress_log": [
                    e.to_dict() if hasattr(e, "to_dict") else e
                    for e in getattr(report, "progress_log", [])
                ],
                "hybrid_telemetry": getattr(report, "hybrid_telemetry", None),
                "swarm_report": getattr(report, "swarm_report", None),
            }

        else:
            # Fast path for normal conversation. The previous implementation ran three
            # expensive LOGOS passes (pondering, topology search, decomposition) before
            # every ordinary chat turn and could also trigger web search for generic questions.
            # Deep analysis is opt-in by prompt; routine chat makes one bounded model call.
            lower_prompt = p_clean.lower()
            deep_mode = any(
                marker in lower_prompt
                for marker in (
                    "analyze deeply",
                    "deep reasoning",
                    "multi-step proof",
                    "formal proof",
                    "exhaustive analysis",
                    "think through every case",
                    "use logos",
                )
            )
            pondering = topology = None
            subqs = []
            if deep_mode:
                pondering = self.operator.pondering.evaluate_multi_angle(p_clean)
                topology = self.operator.nas.search_optimal_topology(p_clean)
                subqs = self.operator.decomposer.decompose(p_clean)

            # Bounded recent context prevents chat history from ballooning with long code/results.
            context_str = ""
            context_history = self._build_context(4)
            if context_history:
                chunks = []
                remaining = 10000
                for turn in reversed(context_history):
                    prompt_part = str(turn.get("prompt", ""))[:1200]
                    answer_part = str(turn.get("result", {}).get("content", ""))[:1800]
                    chunk = f"User: {prompt_part}\nAssistant: {answer_part}\n---\n"
                    if len(chunk) > remaining:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if chunks:
                    context_str = (
                        "Conversation context (recent turns):\n" + "".join(reversed(chunks)) + "\n"
                    )

            # Only do a network search when the user explicitly requests fresh/current information.
            search_supplement = ""
            try:
                explicit_search = any(
                    w in lower_prompt
                    for w in (
                        "search the web",
                        "search online",
                        "latest news",
                        "current price",
                        "as of today",
                        "look up current",
                    )
                )
                if explicit_search:
                    s_res = self.operator.tools.execute("web_search", query=p_clean[:240])
                    if s_res.success and s_res.output:
                        search_supplement = (
                            f"\nRelevant Knowledge / Real-time Context:\n{s_res.output[:1200]}\n"
                        )
            except Exception:
                pass

            # Generate via LLM provider
            system_prompt = (
                "You are an intelligent, helpful AI assistant modeled after Claude and Lightyear Ultra. "
                "Answer the user's question directly, clearly, elegantly, comprehensively, and conversationally. "
                "Provide detailed, long-horizon explanations, structured markdown, headers, bullet points, and code snippets when appropriate. "
                "Do NOT leak internal confidence scores or raw prompting templates."
            )
            try:
                llm_resp = self.operator.llm.generate(
                    prompt=f"{context_str}{search_supplement}{p_clean}",
                    system_prompt=system_prompt,
                    temperature=self.temperature,
                    max_tokens=self.operator._safe_llm_max_tokens(4096 if deep_mode else 2048),
                )
            except (ConnectionError, OSError, Exception) as _llm_err:
                err_result = self._llm_error_result(p_clean, t0, _llm_err)
                self._append_history(p_clean, err_result)
                self._auto_save_chat()
                return err_result

            clean_content = llm_resp.content.strip()
            if "**Response:**" in clean_content:
                clean_content = clean_content.split("**Response:**", 1)[1]
                if "**Analysis:**" in clean_content:
                    clean_content = clean_content.split("**Analysis:**", 1)[0]
                clean_content = clean_content.strip()

            # Keep the fast path light: ordinary chat is already retained in bounded
            # conversation history. Only opt-in deep analysis populates holographic memory.
            if deep_mode:
                self.operator.holographic_mem.store(p_clean[:4000], category="user_query")
                self.operator.holographic_mem.store(
                    clean_content[:4000], category="assistant_reply"
                )
            thinking_data = {
                "mode": "deep" if deep_mode else "fast",
                "topology": topology.topology_type.value if topology else "direct_chat",
                "summary": pondering.synthesis_summary
                if pondering
                else "Fast conversational path: one bounded model call; expensive analysis layers skipped.",
                "subquestions": [sq.question for sq in subqs],
                "qualified_properties": (
                    [
                        {
                            "name": p.name,
                            "confidence": round(p.confidence, 4),
                            "hypothesis": p.hypothesis,
                        }
                        for p in pondering.qualified_properties
                    ]
                    if pondering
                    else []
                ),
                "holographic_stats": (
                    self.operator.holographic_mem.get_boundary_compression_stats()
                    if deep_mode
                    else None
                ),
                "effective_tokens": int(getattr(llm_resp, "total_tokens", 0) or 0),
                "total_tokens_used": int(getattr(llm_resp, "total_tokens", 0) or 0),
                "token_usage_complete": False,
            }

            result = {
                "type": "conversational",
                "content": clean_content,
                "code": None,
                "test_logs": None,
                "status": "success",
                "review": None,
                "thinking": thinking_data,
                "jev": jev_report.to_dict(),
                "provider": self.operator.llm.provider_name,
                "model": self.operator.llm.model_name,
                "elapsed_seconds": round(time.perf_counter() - t0, 2),
                "effective_tokens": int(getattr(llm_resp, "total_tokens", 0) or 0),
                "total_tokens_used": int(getattr(llm_resp, "total_tokens", 0) or 0),
                "token_usage_complete": False,
            }

        self._append_history(p_clean, result)
        self._auto_save_chat()
        return result

    def _append_history(self, prompt: str, result: Dict[str, Any]) -> None:
        """Retain bounded, context-useful history instead of duplicating unbounded artifacts."""
        compact = dict(result)
        for key, limit in (("content", 10000), ("code", 10000), ("test_logs", 3000)):
            value = compact.get(key)
            if isinstance(value, str) and len(value) > limit:
                compact[key] = value[:limit] + "\n…[trimmed in conversation memory]"
        # Detailed traces belong in the run/artifact record, not every future chat prompt.
        if isinstance(compact.get("thinking"), dict):
            thinking = compact["thinking"]
            compact["thinking"] = {
                k: thinking[k]
                for k in (
                    "mode",
                    "topology",
                    "summary",
                    "effective_tokens",
                    "total_tokens_used",
                    "token_usage_complete",
                )
                if k in thinking
            }
        self.history.append({"prompt": str(prompt)[:3000], "result": compact})
        if len(self.history) > 60:
            del self.history[:-60]

    def _build_context(self, max_turns: int) -> List[Dict[str, Any]]:
        """Extract recent conversation history for context."""
        return self.history[-max(1, min(8, max_turns)) :] if self.history else []

    def _auto_save_chat(self) -> None:
        """Automatically saves the current session."""
        if not self.history:
            return
        if not self.current_chat_id:
            self.current_chat_id = f"chat-{uuid.uuid4().hex[:8]}"

        first_prompt = self.history[0].get("prompt", "Untitled Session")
        title = (first_prompt[:40] + "...") if len(first_prompt) > 40 else first_prompt

        self.saved_chats[self.current_chat_id] = {
            "id": self.current_chat_id,
            "title": title,
            "updated_at": time.time(),
            "turns": list(self.history),
        }

    def _llm_error_result(self, p_clean: str, t0: float, error: Exception) -> Dict[str, Any]:
        """Returns a user-friendly result when the LLM provider fails to connect or respond."""
        err_str = str(error)
        prov = self.provider_name.upper()
        if "ConnectionRefused" in err_str or "10061" in err_str or "Failed to connect" in err_str:
            msg = (
                f"⚠️ **{prov} Provider Offline**: Cannot reach `{prov}` at the configured endpoint.\n\n"
                f"- If using **Ollama**: run `ollama serve` in your terminal.\n"
                f"- If using a **Cloud API**: open ⚙️ Settings, paste your API key, and click _Save & Apply_.\n\n"
                f"`Error: {err_str[:200]}`"
            )
        elif "401" in err_str or "Unauthorized" in err_str or "invalid_api_key" in err_str.lower():
            msg = (
                f"🔑 **Invalid API Key for {prov}**: The server rejected your API key.\n\n"
                "Go to ⚙️ Settings → paste a valid API key → Save & Apply."
            )
        else:
            msg = f"❌ **{prov} Error**: {err_str[:300]}"

        return {
            "type": "error",
            "content": msg,
            "code": None,
            "test_logs": None,
            "status": "error",
            "review": None,
            "thinking": None,
            "jev": None,
            "provider": self.provider_name,
            "model": self.model_name,
            "elapsed_seconds": round(time.perf_counter() - t0, 2),
        }

    # -------------------------------------------------------------------------
    # MCP Server Developer Studio API Methods
    # -------------------------------------------------------------------------

    def get_mcp_presets(self) -> List[Dict[str, Any]]:
        """Returns starter MCP server definitions."""
        return [p.to_dict() for p in get_default_mcp_presets()]

    def generate_mcp_server(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Scaffolds complete standalone FastMCP server code and Claude Desktop config."""
        sname = payload.get("server_name", "custom-mcp-server").strip()
        desc = payload.get("description", "Custom MCP Server developed in Agent-IDE Sandbox")
        tools_data = payload.get("tools", [])

        tools: List[MCPToolDefinition] = []
        for t in tools_data:
            params: List[MCPToolParameter] = []
            for p in t.get("parameters", []):
                params.append(
                    MCPToolParameter(
                        name=p.get("name", "arg"),
                        param_type=p.get("param_type", "string"),
                        description=p.get("description", ""),
                        required=p.get("required", True),
                        default=p.get("default", None),
                    )
                )
            tools.append(
                MCPToolDefinition(
                    name=t.get("name", "my_tool"),
                    description=t.get("description", "A custom MCP tool"),
                    parameters=params,
                    handler_code=t.get("handler_code", ""),
                )
            )

        sdef = MCPServerDefinition(
            server_name=sname,
            description=desc,
            tools=tools,
        )

        py_code = MCPServerScaffolder.generate_python_server(sdef)
        mcp_script_path = str((self.workspace_mgr.root / "agentufo_mcp" / "server.py").resolve())
        cfg_json = MCPServerScaffolder.generate_claude_desktop_config(
            sdef, script_path=mcp_script_path
        )

        # Store as artifact
        self.artifacts.append(
            {
                "id": f"art-mcp-{sname}",
                "title": f"MCP Server: {sname}",
                "type": "mcp_server",
                "language": "python",
                "code": py_code,
                "created_at": time.time(),
                "status": "ready",
            }
        )

        return {
            "status": "success",
            "server_definition": sdef.to_dict(),
            "python_code": py_code,
            "claude_desktop_config": cfg_json,
        }

    def test_mcp_tool(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Runs JSON-RPC sandbox execution for an MCP tool."""
        tool_data = payload.get("tool", {})
        arguments = payload.get("arguments", {})

        params: List[MCPToolParameter] = []
        for p in tool_data.get("parameters", []):
            params.append(
                MCPToolParameter(
                    name=p.get("name", "arg"),
                    param_type=p.get("param_type", "string"),
                    description=p.get("description", ""),
                    required=p.get("required", True),
                )
            )

        tool = MCPToolDefinition(
            name=tool_data.get("name", "test_tool"),
            description=tool_data.get("description", "A tool being tested"),
            parameters=params,
            handler_code=tool_data.get("handler_code", ""),
        )

        return self.mcp_tester.test_tool_execution(tool, arguments)

    # -------------------------------------------------------------------------
    # Projects & Artifacts Management
    # -------------------------------------------------------------------------

    def get_projects(self) -> List[Dict[str, Any]]:
        """Returns all available and pinned projects."""
        return self.projects

    def create_project(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Creates a new user project."""
        title = payload.get("title", "New Project").strip()
        description = payload.get("description", "").strip()
        proj = {
            "id": f"proj-{uuid.uuid4().hex[:8]}",
            "title": title,
            "description": description,
            "pinned": True,
            "created_at": time.time(),
        }
        self.projects.insert(0, proj)
        return proj

    def get_artifacts(self) -> List[Dict[str, Any]]:
        """Returns all generated artifacts."""
        return self.artifacts

    # -------------------------------------------------------------------------
    # Chat History & Persistence
    # -------------------------------------------------------------------------

    def get_chats(self) -> List[Dict[str, Any]]:
        """Returns list of recent chats metadata."""
        items = []
        for cid, cdata in self.saved_chats.items():
            items.append(
                {
                    "id": cid,
                    "title": cdata.get("title", "Untitled Chat"),
                    "updated_at": cdata.get("updated_at", time.time()),
                    "turn_count": len(cdata.get("turns", [])),
                }
            )
        items.sort(key=lambda x: x["updated_at"], reverse=True)
        return items

    def load_chat(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """Loads a specific chat session."""
        return self.saved_chats.get(chat_id)

    def save_chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Saves or updates a chat session."""
        chat_id = payload.get("id") or f"chat-{uuid.uuid4().hex[:8]}"
        title = payload.get("title") or (
            self.history[0]["prompt"][:35] if self.history else "New Chat"
        )
        turns = payload.get("turns") or self.history

        self.saved_chats[chat_id] = {
            "id": chat_id,
            "title": title,
            "updated_at": time.time(),
            "turns": turns,
        }
        return {"status": "saved", "chat_id": chat_id, "title": title}

    def clear_session(self) -> Dict[str, Any]:
        """Resets chat history and holographic boundary traces."""
        # Archive current conversation if not empty
        if self.history:
            self._auto_save_chat()

        self.history.clear()
        self.current_chat_id = None
        _clear_kwargs: Dict[str, Any] = {}
        if self.provider_name == "ollama" and self.custom_endpoint:
            _clear_kwargs["host"] = self.custom_endpoint
        elif self.provider_name in ["local", "openai"] and self.custom_endpoint:
            _clear_kwargs["base_url"] = self.custom_endpoint
        self.operator = SandboxOperatorInterface(
            provider_name=self.provider_name,
            model_name=self.model_name,
            enable_logos=self.enable_logos,
            enable_hybrid=self.enable_hybrid,
            **_clear_kwargs,
        )
        self._sync_coordinator_provider()
        return {"status": "cleared", "history_count": 0}

    def test_connection(self) -> Dict[str, Any]:
        """
        Sends a lightweight ping to the active LLM provider to verify connectivity.
        Returns provider info, response time, and detailed status.
        """
        import time as _time

        t0 = _time.perf_counter()

        provider = self.operator.llm.provider_name
        model = self.operator.llm.model_name

        try:
            resp = self.operator.llm.generate(
                prompt="Reply with exactly: PING_OK",
                system_prompt="You are a connectivity test. Reply with exactly: PING_OK",
                max_tokens=15,
                temperature=0.0,
            )
            latency_ms = round((_time.perf_counter() - t0) * 1000, 1)
            content = resp.content.strip()
            return {
                "status": "ok",
                "provider": provider,
                "model": resp.model or model,
                "message": f"✅ Connection successful! ({provider.upper()}: {resp.model or model}) — {latency_ms}ms",
                "latency_ms": latency_ms,
                "ping_response": content[:80],
                "tokens_used": resp.total_tokens,
            }
        except Exception as e:
            latency_ms = round((_time.perf_counter() - t0) * 1000, 1)
            err_str = str(e)
            if (
                "401" in err_str
                or "Unauthorized" in err_str
                or "invalid_api_key" in err_str.lower()
            ):
                msg = f"❌ Invalid API Key for {provider.upper()}. Check your key in Settings."
            elif "timeout" in err_str.lower() or "Connection" in err_str:
                msg = f"❌ Cannot reach {provider.upper()} server. Check your endpoint or internet connection."
            elif "404" in err_str or "model_not_found" in err_str.lower():
                msg = f"❌ Model '{model}' not found on {provider.upper()}. Verify the model name."
            else:
                msg = f"❌ Connection failed ({provider.upper()}): {err_str[:120]}"
            return {
                "status": "error",
                "provider": provider,
                "model": model,
                "message": msg,
                "latency_ms": latency_ms,
                "error": err_str[:200],
            }

    def evaluate_jev(self, prompt: str) -> Dict[str, Any]:
        """Runs raw TypeSafe AI Jev evaluation and returns typed decision report."""
        report = self.jev_engine.evaluate(prompt)
        return report.to_dict()

    # -------------------------------------------------------------------------
    # Ollama Local AI Studio & Auto-Launcher
    # -------------------------------------------------------------------------

    def get_ollama_status(self) -> Dict[str, Any]:
        """Returns Ollama detection status, daemon running state, and installed models."""
        return self.ollama_launcher.get_status()

    def get_ollama_gallery(self) -> List[Dict[str, Any]]:
        """Returns curated model catalog with live installation flags."""
        return self.ollama_launcher.get_gallery()

    def start_ollama_daemon(self) -> Dict[str, Any]:
        """Launches the Ollama daemon in background."""
        return self.ollama_launcher.start_daemon()

    def pull_ollama_model(self, model: str):
        """Streams pull progress generator."""
        return self.ollama_launcher.pull_model_stream(model)

    def connect_ollama_model(self, model_tag: str) -> Dict[str, Any]:
        """
        Auto-launches Ollama daemon (if not running), auto-pulls the model if not installed,
        and switches active provider to Ollama with the selected model.
        """
        # Ensure daemon is running
        if not self.ollama_launcher.is_daemon_running():
            launch_res = self.ollama_launcher.start_daemon()
            if not launch_res.get("success"):
                return {
                    "status": "error",
                    "error": launch_res.get("error", "Failed to start Ollama daemon"),
                    "message": launch_res.get("message", "Ollama daemon could not be started."),
                    "need_install": ("not found" in launch_res.get("error", "").lower()),
                    "download_url": "https://ollama.com/download",
                }

        # Auto-pull model if not already installed
        installed = set(self.ollama_launcher.list_installed())
        model_base = model_tag.split(":")[0]
        is_installed = (
            model_tag in installed
            or f"{model_tag}:latest" in installed
            or any(t.split(":")[0] == model_base for t in installed)
        )

        if not is_installed:
            # Kick off a synchronous pull (streamed internally; we collect final status)
            pull_status = "pending"
            pull_error = ""
            for evt in self.ollama_launcher.pull_model_stream(model_tag):
                if evt.get("done"):
                    pull_status = evt.get("status", "success")
                    break
                if evt.get("status") == "error":
                    pull_error = evt.get("error", "pull failed")
                    break
            if pull_error:
                return {
                    "status": "error",
                    "error": pull_error,
                    "message": f"Auto-pull of '{model_tag}' failed: {pull_error}",
                }

        self.provider_name = "ollama"
        self.model_name = model_tag

        kwargs: Dict[str, Any] = {"host": self.custom_endpoint or "http://127.0.0.1:11434"}

        self.operator = SandboxOperatorInterface(
            provider_name=self.provider_name,
            model_name=self.model_name,
            enable_logos=self.enable_logos,
            enable_hybrid=self.enable_hybrid,
            **kwargs,
        )
        self._sync_coordinator_provider()

        return {
            "status": "connected",
            "provider": self.provider_name,
            "model": self.model_name,
            "auto_pulled": not is_installed,
            "message": f"{'Auto-downloaded and connected' if not is_installed else 'Connected'} to local Ollama with {model_tag}!",
            "runtime": self.get_status(),
        }

    # -------------------------------------------------------------------------
    # Project File Explorer & Workspace Management (Cline / VSCode Split View)
    # -------------------------------------------------------------------------

    def open_project(self, folder_path: str) -> Dict[str, Any]:
        """Sets the active project folder, dynamically rebasing the WorkspaceManager, Review Gate, and Locks."""
        target = Path(folder_path).resolve()
        if not target.exists():
            # If folder doesn't exist, create it
            try:
                target.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return {"status": "error", "error": f"Could not create folder: {e}"}

        self.project_root = target
        # Dynamically rebase WorkspaceManager and Coordinator to real disk path
        self.workspace_mgr.rebase_root(target)
        self.agentufo_coordinator.workspace = self.workspace_mgr
        self.capability_prober.workspace_root = target

        tree = self.get_project_tree(str(target))
        return {
            "status": "success",
            "project_path": str(target),
            "project_name": target.name,
            "locks_file": str(self.workspace_mgr.locks_file),
            "staging_dir": str(self.workspace_mgr.staging_dir),
            "tree": tree,
        }

    def get_project_tree(
        self, root_dir: Optional[str] = None, max_depth: int = 4
    ) -> Dict[str, Any]:
        """Builds a recursive tree of the project folder for the right split pane explorer."""
        active_root = Path(getattr(self, "project_root", Path(os.getcwd()).resolve())).resolve()
        base_path = Path(root_dir).resolve() if root_dir else active_root
        if not base_path.is_relative_to(active_root):
            return {
                "status": "error",
                "error": "Requested directory is outside the active project workspace",
            }
        if not base_path.exists() or not base_path.is_dir():
            return {
                "status": "error",
                "error": "Project directory does not exist or is not a directory",
            }

        def build_node(p: Path, depth: int) -> Dict[str, Any]:
            node: Dict[str, Any] = {
                "name": p.name,
                "path": str(p),
                "rel_path": str(p.relative_to(base_path)).replace("\\", "/"),
                "is_dir": p.is_dir(),
            }
            if p.is_dir():
                if depth >= max_depth:
                    node["children"] = []
                    return node
                children = []
                try:
                    for child in sorted(
                        p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())
                    ):
                        # Skip hidden/cache items
                        if child.name.startswith(".") or child.name in (
                            "node_modules",
                            "__pycache__",
                            "venv",
                            ".git",
                            ".venv",
                        ):
                            continue
                        children.append(build_node(child, depth + 1))
                except PermissionError:
                    pass
                node["children"] = children
            else:
                sz = p.stat().st_size if p.exists() else 0
                node["size"] = sz
                node["ext"] = p.suffix.lower()
            return node

        return {
            "root_path": str(base_path),
            "root_name": base_path.name,
            "tree": build_node(base_path, depth=0),
        }

    def _resolve_project_path(self, file_path: str) -> Path:
        """Resolve a requested path and reject traversal or symlink escapes."""
        base = Path(getattr(self, "project_root", Path(os.getcwd()).resolve())).resolve()
        candidate = Path(file_path)
        candidate = candidate if candidate.is_absolute() else base / candidate
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(base):
            raise ValueError("Path is outside the active project workspace")
        return resolved

    def read_project_file(self, file_path: str) -> Dict[str, Any]:
        """Reads a file only from the active workspace, including symlink resolution."""
        try:
            p = self._resolve_project_path(file_path)
        except (OSError, ValueError) as e:
            return {"status": "error", "error": str(e)}
        if not p.exists() or not p.is_file():
            return {"status": "error", "error": f"File not found: {file_path}"}

        try:
            if p.stat().st_size > 5 * 1024 * 1024:
                return {"status": "error", "error": "File exceeds the 5 MiB editor safety limit"}
            content = p.read_text(encoding="utf-8", errors="replace")
            return {
                "status": "success",
                "path": str(p),
                "rel_path": str(
                    p.relative_to(getattr(self, "project_root", Path(os.getcwd()).resolve()))
                    if getattr(self, "project_root", None) and p.is_relative_to(self.project_root)
                    else p.name
                ).replace("\\", "/"),
                "name": p.name,
                "ext": p.suffix.lower(),
                "content": content,
                "lines": len(content.splitlines()),
                "size": p.stat().st_size,
            }
        except Exception as e:
            return {"status": "error", "error": f"Could not read file: {e}"}

    def write_project_file(self, file_path: str, content: str) -> Dict[str, Any]:
        """Writes only inside the active workspace; rejects traversal and symlink escapes."""
        if not isinstance(content, str):
            return {"status": "error", "error": "File content must be a string"}
        if len(content.encode("utf-8")) > 5 * 1024 * 1024:
            return {"status": "error", "error": "File content exceeds the 5 MiB safety limit"}
        try:
            p = self._resolve_project_path(file_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            # Re-resolve after mkdir to catch pre-existing symlink parents.
            p = p.resolve(strict=False)
            base = Path(getattr(self, "project_root", Path(os.getcwd()).resolve())).resolve()
            if not p.is_relative_to(base):
                raise ValueError("Path is outside the active project workspace")
            workspace_mgr = getattr(self, "workspace_mgr", None)
            if workspace_mgr is not None and Path(workspace_mgr.root).resolve() == base:
                workspace_mgr.write_file_atomic(
                    p.relative_to(base), content, seat_id="master_admin"
                )
            else:
                # Safe fallback for isolated tests/tools that construct the manager without __init__.
                import tempfile

                temp_name = None
                try:
                    with tempfile.NamedTemporaryFile(
                        "w", encoding="utf-8", dir=p.parent, delete=False
                    ) as temp_file:
                        temp_name = temp_file.name
                        temp_file.write(content)
                        temp_file.flush()
                        os.fsync(temp_file.fileno())
                    os.replace(temp_name, p)
                finally:
                    if temp_name and os.path.exists(temp_name):
                        os.unlink(temp_name)
            return {
                "status": "success",
                "path": str(p),
                "name": p.name,
                "bytes_written": len(content.encode("utf-8")),
            }
        except Exception as e:
            return {"status": "error", "error": f"Could not write file: {e}"}

    # -------------------------------------------------------------------------
    # Frontend API Key Auto-Detector & Dynamic Rebinding
    # -------------------------------------------------------------------------

    def detect_api_key(self, raw_key: str) -> Dict[str, Any]:
        """
        Generic API key auto-detection and apply.
        Uses LLMRegistry.generic_detect_provider_and_model for pattern matching + online probe.
        Updates env vars, .env file, and rebinds the operator — no hardcoded provider logic.
        """
        key = raw_key.strip()
        if not key:
            return {
                "status": "unknown",
                "detected": False,
                "provider": None,
                "message": "No API key provided.",
            }

        # Generic detection: pattern + online probe
        detection = LLMRegistry.generic_detect_provider_and_model(key, probe_online=True)

        if not detection.get("detected"):
            return {
                "status": "unknown",
                "detected": False,
                "provider": None,
                "message": detection.get("message", "Key format not recognized."),
            }

        provider = detection["provider"]
        default_model = detection["model"]
        env_var = detection.get("env_var", "")

        # Persist to env
        if env_var:
            os.environ[env_var] = key
            try:
                _update_env_file(_env_file, env_var, key)
            except Exception:
                pass

        # Rebind operator to new provider
        try:
            self.operator.set_llm(provider, default_model)
        except Exception as exc:
            return {
                "status": "error",
                "detected": True,
                "provider": provider,
                "message": f"Key was detected but provider rebinding failed: {exc}",
            }
        self.provider_name = provider
        self.model_name = default_model
        self._sync_coordinator_provider()
        self._status_cache = None
        self._status_cache_at = 0.0

        return {
            "status": "success",
            "detected": True,
            "verified": detection.get("verified", False),
            "provider": provider,
            "provider_name": detection.get("provider_name", provider.upper()),
            "model": default_model,
            "models": detection.get("models", []),
            "env_var": env_var,
            "message": detection.get(
                "message", f"Auto-detected {provider.upper()}! Active model: {default_model}."
            ),
            "runtime": self.get_status(),
        }
