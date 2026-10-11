"""
agentufo_swarm.py
=================
AgentUFO Multi-Agent Swarm Orchestrator & Durable Crew Runtime.
Replaces BridgeMind with production-grade autonomous crew coordination:

1. Real Workspace Integration: Edits real disk paths via WorkspaceManager with mutual-exclusion locks.
2. Review Gate Enforcement: Reviewer seat executes tests in sandbox; can veto and reject.
3. Durable Crew State & Mailbox: Survives page refreshes, browser reloads, and server crashes.
4. Hard Budget Guard: Enforces total, planner, and per-seat caps. Zero silent fallback!
5. Machine Capability Veto: Local models probed empirically; weak/broken models are vetoed.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from agent_core.llm.base import BaseLLMProvider, LLMResponse
from agent_core.llm.registry import LLMRegistry
from agent_core.sandbox import CodeExecutionSandbox
from agent_core.workspace_manager import WorkspaceManager, PathLockConflictError
from agent_core.review_gate import ReviewGate, ReviewGateReport, ReviewGateRejectedError
from agent_core.budget_guard import HardBudgetGuard, BudgetConfig, BudgetCapExceededError, LocalRuntimeUnavailableError
from agent_core.capability_prober import EmpiricalCapabilityProber


class AgentUFOMode(str, Enum):
    CREW_PIPELINE = "crew_pipeline"      # Architect -> Builder -> Reviewer Gate
    SPLIT_WORK = "split_work"            # Antichain DAG partitioned parallel seats
    COMPETITIVE_CONSENSUS = "consensus"  # Tournament with sandbox test ranking


class SeatTokenPolicy(str, Enum):
    ALL = "all"
    ALL_TIERED = "all"                   # All seats use hybrid local tiering where possible
    LEADER_ONLY = "leader_only"
    LEADER_ONLY_CLOUD = "leader_only"    # Leader/Architect uses Cloud API; Builders use local models
    CLOUD_ONLY = "cloud_only"            # Pure Frontier Cloud API across all seats


@dataclass
class CrewSeat:
    """Represents an active seat in the AgentUFO crew with multi-LLM assignment & live control."""
    seat_id: str
    role_name: str
    description: str
    assigned_paths: List[str] = field(default_factory=list)
    status: str = "idle"  # idle, thinking, generating, paused, stopped, completed, rejected
    tier_used: str = "cloud"
    tokens_consumed: int = 0
    token_usage_estimated: bool = True
    calls_made: int = 0
    assigned_model: Optional[str] = None
    assigned_provider: Optional[str] = None
    is_paused: bool = False
    is_stopped: bool = False
    current_thought: str = ""
    live_tokens: int = 0
    tokens_per_sec: float = 0.0
    injected_context: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seat_id": self.seat_id,
            "role_name": self.role_name,
            "description": self.description,
            "assigned_paths": self.assigned_paths,
            "status": self.status,
            "tier_used": self.tier_used,
            "tokens_consumed": self.tokens_consumed,
            "token_usage_estimated": self.token_usage_estimated,
            "calls_made": self.calls_made,
            "assigned_model": self.assigned_model,
            "assigned_provider": self.assigned_provider,
            "is_paused": self.is_paused,
            "is_stopped": self.is_stopped,
            "current_thought": self.current_thought,
            "live_tokens": self.live_tokens,
            "tokens_per_sec": self.tokens_per_sec,
            "injected_context": self.injected_context,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> CrewSeat:
        return cls(
            seat_id=d["seat_id"],
            role_name=d["role_name"],
            description=d["description"],
            assigned_paths=d.get("assigned_paths", []),
            status=d.get("status", "idle"),
            tier_used=d.get("tier_used", "cloud"),
            tokens_consumed=d.get("tokens_consumed", 0),
            token_usage_estimated=d.get("token_usage_estimated", True),
            calls_made=d.get("calls_made", 0),
            assigned_model=d.get("assigned_model"),
            assigned_provider=d.get("assigned_provider"),
            is_paused=d.get("is_paused", False),
            is_stopped=d.get("is_stopped", False),
            current_thought=d.get("current_thought", ""),
            live_tokens=d.get("live_tokens", 0),
            tokens_per_sec=d.get("tokens_per_sec", 0.0),
            injected_context=d.get("injected_context", ""),
        )


@dataclass
class BriefMessage:
    """Inter-seat mailbox message."""
    message_id: str
    from_seat: str
    to_seat: str
    msg_type: str  # "architectural_brief", "pull_request", "review_verdict", "repair_request"
    title: str
    payload: Dict[str, Any]
    timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "from_seat": self.from_seat,
            "to_seat": self.to_seat,
            "msg_type": self.msg_type,
            "title": self.title,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> BriefMessage:
        return cls(
            message_id=d["message_id"],
            from_seat=d["from_seat"],
            to_seat=d["to_seat"],
            msg_type=d["msg_type"],
            title=d["title"],
            payload=d.get("payload", {}),
            timestamp=d.get("timestamp", time.time()),
        )


@dataclass
class AgentUFOExecutionReport:
    """Comprehensive final report of AgentUFO crew execution."""
    task: str
    mode: str
    status: str  # "success", "rejected", "budget_exceeded", "error"
    final_code: str
    target_filepaths: List[str]
    seats: List[CrewSeat]
    mailbox_messages: List[BriefMessage]
    review_report: Optional[ReviewGateReport] = None
    budget_telemetry: Optional[Dict[str, Any]] = None
    elapsed_seconds: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "mode": self.mode,
            "status": self.status,
            "final_code": self.final_code,
            "target_filepaths": self.target_filepaths,
            "seats": [s.to_dict() for s in self.seats],
            "mailbox_count": len(self.mailbox_messages),
            "review": self.review_report.to_dict() if self.review_report else None,
            "budget": self.budget_telemetry,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "error": self.error,
        }


class CrewSeatStopped(RuntimeError):
    """Cooperative stop signal emitted at a safe agent checkpoint."""


class AgentUFOCoordinator:
    """
    AgentUFO Orchestrator managing real workspace file mutations,
    inter-seat durable mailboxes, empirical local capability probing,
    strict budget guards, and review gate rejection authority.
    """

    def __init__(
        self,
        cloud_llm: BaseLLMProvider,
        local_llm: Optional[BaseLLMProvider] = None,
        workspace: Optional[WorkspaceManager] = None,
        num_seats: int = 3,
        mode: AgentUFOMode = AgentUFOMode.CREW_PIPELINE,
        token_policy: SeatTokenPolicy = SeatTokenPolicy.ALL_TIERED,
        budget_guard: Optional[HardBudgetGuard] = None,
        review_gate: Optional[ReviewGate] = None,
        capability_prober: Optional[EmpiricalCapabilityProber] = None,
    ) -> None:
        self.cloud_llm = cloud_llm
        self.local_llm = local_llm
        self.workspace = workspace or WorkspaceManager()
        self.num_seats = max(2, min(16, num_seats))
        self.mode = mode
        self.token_policy = token_policy
        self.budget_guard = budget_guard or HardBudgetGuard()
        self.review_gate = review_gate or ReviewGate()
        self.capability_prober = capability_prober or EmpiricalCapabilityProber(self.workspace.root)

        # Durable state directory
        self.state_file = self.workspace.agentufo_dir / "crew_state.json"
        self.mailbox_file = self.workspace.agentufo_dir / "mailbox.json"

        self.seats: List[CrewSeat] = []
        self.mailbox: List[BriefMessage] = []
        self._init_crew_seats()
        self._load_durable_state()

    def _init_crew_seats(self) -> None:
        """Initializes specialized crew seats."""
        base_roles = [
            ("seat_1_architect", "Architect & Planner", "Decomposes requests, defines system blueprints, assigns paths, and writes specs."),
            ("seat_2_builder", "Builder & Implementer", "Writes code to disk, validates syntax, and constructs tests."),
            ("seat_3_reviewer", "Reviewer & Gatekeeper", "Runs sandbox test suite with veto and rejection authority."),
            ("seat_4_refactorer", "Refactorer & Optimizer", "Performs AST minimization and runtime speed tuning."),
            ("seat_5_security", "Security Auditor", "Verifies zero arbitrary subprocess execution or bytecode leaks."),
        ]
        self.seats = []
        for i in range(self.num_seats):
            preset = base_roles[i] if i < len(base_roles) else (f"seat_{i+1}_worker", f"Specialized Worker {i+1}", "Executes decomposed subtasks.")
            self.seats.append(CrewSeat(
                seat_id=preset[0],
                role_name=preset[1],
                description=preset[2],
            ))

    def _load_durable_state(self) -> None:
        """Loads mailbox and crew state from disk to survive page refresh."""
        if self.mailbox_file.exists():
            try:
                with open(self.mailbox_file, "r", encoding="utf-8") as f:
                    msgs = json.load(f)
                self.mailbox = [BriefMessage.from_dict(m) for m in msgs]
            except Exception:
                self.mailbox = []

        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                loaded_seats = [CrewSeat.from_dict(s) for s in data.get("seats", [])]
                if loaded_seats and len(loaded_seats) == len(self.seats):
                    self.seats = loaded_seats
            except Exception:
                pass

    def _persist_durable_state(self) -> None:
        """Atomically saves crew state and mailbox to disk."""
        self.workspace.agentufo_dir.mkdir(parents=True, exist_ok=True)

        # 1. Save mailbox
        m_temp = self.mailbox_file.with_suffix(".tmp")
        with open(m_temp, "w", encoding="utf-8") as f:
            json.dump([m.to_dict() for m in self.mailbox], f, indent=2)
        shutil.move(str(m_temp), str(self.mailbox_file))

        # 2. Save seats state
        s_temp = self.state_file.with_suffix(".tmp")
        with open(s_temp, "w", encoding="utf-8") as f:
            json.dump({
                "seats": [s.to_dict() for s in self.seats],
                "updated_at": time.time(),
                "mode": self.mode.value,
                "token_policy": self.token_policy.value,
                "seat_models": getattr(self, "seat_models", {}),
            }, f, indent=2)
        shutil.move(str(s_temp), str(self.state_file))

    def pause_seat(self, seat_id: str) -> bool:
        """Pauses execution of a specific seat."""
        for s in self.seats:
            if s.seat_id == seat_id:
                s.is_paused = True
                s.status = "paused"
                self._persist_durable_state()
                return True
        return False

    def resume_seat(self, seat_id: str) -> bool:
        """Resumes a paused seat."""
        for s in self.seats:
            if s.seat_id == seat_id:
                s.is_paused = False
                s.status = "generating"
                self._persist_durable_state()
                return True
        return False

    def stop_seat(self, seat_id: str) -> bool:
        """Cancels a specific seat immediately."""
        for s in self.seats:
            if s.seat_id == seat_id:
                s.is_stopped = True
                s.status = "stopped"
                self._persist_durable_state()
                return True
        return False

    def inject_context(self, seat_id: str, context: str) -> bool:
        """Injects human guidance/prompt edit into an agent mid-flight."""
        for s in self.seats:
            if s.seat_id == seat_id:
                prior = (s.injected_context or "").strip()
                incoming = str(context or "").strip()
                combined = (prior + "\n\n--- newer shared update ---\n" + incoming).strip() if prior else incoming
                s.injected_context = combined[-12000:]
                msg = BriefMessage(
                    message_id=f"msg_{uuid.uuid4().hex[:8]}",
                    from_seat="User Steering",
                    to_seat=seat_id,
                    msg_type="USER_INJECTION",
                    title="User Guided Correction",
                    payload={"instruction": context},
                    timestamp=time.time(),
                )
                self.mailbox.append(msg)
                self._persist_durable_state()
                return True
        return False

    def get_cockpit_telemetry(self) -> Dict[str, Any]:
        """Returns live multi-seat token generation & thought telemetry."""
        return {
            "mode": self.mode.value,
            "num_seats": len(self.seats),
            "seats": [s.to_dict() for s in self.seats],
            "total_tokens": sum(s.tokens_consumed for s in self.seats),
            "total_calls": sum(s.calls_made for s in self.seats),
            "mailbox_count": len(self.mailbox),
            "recent_messages": [m.to_dict() for m in self.mailbox[-8:]],
            "locks": [l.to_dict() for l in self.workspace.list_active_locks()],
        }

    def post_message(self, from_seat: str, to_seat: str, msg_type: str, title: str, payload: Dict[str, Any]) -> BriefMessage:
        """Appends an inter-seat brief message and persists state."""
        msg = BriefMessage(
            message_id=f"msg_{uuid.uuid4().hex[:8]}",
            from_seat=from_seat,
            to_seat=to_seat,
            msg_type=msg_type,
            title=title,
            payload=payload,
            timestamp=time.time(),
        )
        self.mailbox.append(msg)
        self._persist_durable_state()
        return msg

    def get_mailbox_for_seat(self, seat_id: str) -> List[BriefMessage]:
        return [m for m in self.mailbox if m.to_seat == seat_id or m.to_seat == "all"]

    def _resolve_llm_and_tier(self, seat_index: int, seat_id: str, is_planner: bool = False) -> Tuple[BaseLLMProvider, str]:
        """
        Resolves model provider for a seat, checking veto authority and preventing silent fallback.
        """
        seat = next((item for item in self.seats if item.seat_id == seat_id), None)
        # An explicit seat binding is authoritative. Never display one model and silently execute another.
        if seat and seat.assigned_provider and seat.assigned_model:
            provider_name = str(seat.assigned_provider).strip().lower()
            model_name = str(seat.assigned_model).strip()
            try:
                provider = LLMRegistry.get_provider(provider_name, model_name)
            except Exception as exc:
                raise RuntimeError(f"Explicit model assignment {provider_name}/{model_name} for {seat_id} could not be initialized: {exc}") from exc
            if not provider.is_available():
                raise RuntimeError(f"Explicit model assignment {provider_name}/{model_name} for {seat_id} is unavailable. No silent fallback was attempted.")
            if provider_name == "ollama":
                vetoed, reason = self.capability_prober.is_vetoed(model_name)
                if vetoed:
                    raise RuntimeError(f"Local model {model_name} was vetoed by the capability probe for {seat_id}: {reason}. Select a capable model explicitly.")
            seat.tier_used = "local" if provider_name == "ollama" else "cloud"
            return provider, seat.tier_used

        has_local = self.local_llm is not None and self.local_llm.is_available()

        if self.token_policy == SeatTokenPolicy.CLOUD_ONLY:
            return self.cloud_llm, "cloud"

        if self.token_policy == SeatTokenPolicy.LEADER_ONLY_CLOUD and seat_index == 0:
            return self.cloud_llm, "cloud"

        # Seat is eligible for local tiering
        if not has_local:
            # Save-token mode is strict: never consume cloud tokens because local inference is unavailable.
            self.budget_guard.verify_local_runtime_or_fail(seat_id, is_available=False)
            raise RuntimeError(f"Save Tokens is enabled, but no available local model can serve {seat_id}. No cloud fallback was attempted.")

        # Check empirical veto on the local model!
        model_name = getattr(self.local_llm, "model_name", "unknown")
        vetoed, reason = self.capability_prober.is_vetoed(model_name)
        if vetoed:
            raise RuntimeError(f"Local model {model_name} was vetoed by the capability probe for {seat_id}: {reason}. No cloud fallback was attempted.")

        return self.local_llm, "local"

    def _generate_with_budget(self, seat: CrewSeat, llm: BaseLLMProvider, prompt: str, *, is_planner: bool = False, is_local_tier: bool = False) -> LLMResponse:
        """Bound each provider call by remaining token budgets and record observed usage."""
        controlled = self._controlled_prompt(seat, prompt)
        estimated_prompt = max(64, len(controlled) // 4)
        guard = self.budget_guard
        guard.check_headroom(seat.seat_id, is_planner=is_planner, estimated_tokens=estimated_prompt + 1, is_local_tier=is_local_tier)
        remaining = guard.config.total_token_cap - guard.total_tokens_used - estimated_prompt
        remaining = min(remaining, guard.config.per_seat_token_cap - guard.seat_tokens_used.get(seat.seat_id, 0) - estimated_prompt)
        if is_planner:
            remaining = min(remaining, guard.config.planner_token_cap - guard.planner_tokens_used - estimated_prompt)
        max_output = min(4096, remaining)
        if max_output < 1:
            guard.is_halted = True
            guard.halt_reason = f"No token headroom remains for {seat.seat_id} after reserving the prompt budget."
            raise BudgetCapExceededError(guard.halt_reason)
        seat.current_thought = f"Generating with {getattr(llm, 'model_name', 'assigned model')}"
        response = llm.generate(controlled, max_tokens=max_output)
        actual_prompt = int(getattr(response, "prompt_tokens", 0) or estimated_prompt)
        actual_completion = int(getattr(response, "completion_tokens", 0) or max(1, len(getattr(response, "content", "")) // 4))
        guard.record_usage(seat.seat_id, actual_prompt, actual_completion, is_planner=is_planner, is_local_tier=is_local_tier)
        seat.tokens_consumed += actual_prompt + actual_completion
        seat.calls_made += 1
        seat.token_usage_estimated = not bool(getattr(response, "prompt_tokens", 0) and getattr(response, "completion_tokens", 0))
        # A provider can ignore requested limits. Fail closed before any later phase/merge.
        if guard.total_tokens_used > guard.config.total_token_cap or guard.seat_tokens_used[seat.seat_id] > guard.config.per_seat_token_cap or (is_planner and guard.planner_tokens_used > guard.config.planner_token_cap):
            guard.is_halted = True
            guard.halt_reason = f"Observed provider usage exceeded a configured token cap for {seat.seat_id}; no further model calls are allowed."
            raise BudgetCapExceededError(guard.halt_reason)
        return response

    def _workspace_context(self, max_chars: int = 10000) -> str:
        """Build a bounded, secret-aware snapshot from the real workspace on disk."""
        ignored_names = {".env", ".env.local", ".env.production", "secrets.json", "credentials.json", "id_rsa", "id_ed25519"}
        allowed = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json", ".md", ".toml", ".yaml", ".yml", ".go", ".rs", ".java", ".cpp", ".h", ".sql"}
        files: List[str] = []
        def walk(nodes: List[Dict[str, Any]]) -> None:
            for node in nodes:
                name = str(node.get("name") or "")
                lowered_name = name.lower()
                if name in ignored_names or (Path(name).suffix.lower() == ".json" and lowered_name not in {"package.json", "tsconfig.json"}) or any(word in lowered_name for word in ("secret", "credential", "api_key", "apikey", "token", "password")) or lowered_name.endswith((".pem", ".p12", ".key", ".secret")):
                    continue
                if node.get("is_dir"):
                    if name not in {".git", ".agentufo", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}:
                        walk(node.get("children", []))
                elif Path(name).suffix.lower() in allowed and int(node.get("size", 0) or 0) <= 50000:
                    files.append(str(node.get("path") or name))
        try:
            walk(self.workspace.get_workspace_tree(max_depth=3))
        except Exception:
            return "Workspace tree could not be read; use only paths explicitly provided in the task."
        out = ["Workspace files (relative paths):"]
        out.extend(f"- {p}" for p in files[:80])
        remaining = max_chars - sum(len(x) + 1 for x in out)
        out.append("\nSelected source excerpts (secrets and hidden runtime files are excluded):")
        remaining -= len(out[-1]) + 1
        for path in files[:12]:
            if remaining <= 300:
                break
            try:
                content = self.workspace.read_file(path)[:min(1800, remaining)]
            except Exception:
                continue
            block = f"\n--- {path} ---\n{content}"
            out.append(block)
            remaining -= len(block)
        return "\n".join(out)[:max_chars]

    def _choose_target_path(self, task: str, architecture: str, fallback: str) -> str:
        """Choose a workspace-relative file path with traversal and sensitive-path rejection."""
        import re
        extensions = r"(?:py|js|mjs|ts|tsx|jsx|html|css|json|md|yaml|yml|toml|go|rs|java|cpp|h|sql)"
        pattern = re.compile(rf"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+[\\/])*[A-Za-z0-9_.-]+\.(?:{extensions})(?![A-Za-z0-9_.-])")
        explicit = []
        create_intent = any(word in task.lower() for word in ("create", "add", "new file", "write", "generate"))
        allowed_suffixes = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".json", ".md", ".yaml", ".yml", ".toml", ".go", ".rs", ".java", ".cpp", ".h", ".sql"}
        for match in pattern.finditer(task):
            candidate = match.group(0).replace("\\", "/")
            if ".." in Path(candidate).parts or candidate.startswith("/") or candidate.split("/")[0] in {".git", ".agentufo", "node_modules", ".venv"} or Path(candidate).suffix.lower() not in allowed_suffixes:
                continue
            try:
                resolved = (self.workspace.root / candidate).resolve(strict=False)
                if resolved.is_relative_to(self.workspace.root) and ((resolved.exists() and resolved.is_file()) or create_intent):
                    explicit.append(candidate)
            except (OSError, ValueError):
                continue
        if explicit:
            return explicit[0]
        planned = re.search(r"(?im)^\s*TARGET_FILE\s*:\s*([^\r\n`]+)", architecture)
        candidate = planned.group(1).strip().strip("`'\"") if planned else fallback
        candidate = candidate.replace("\\", "/")
        if not candidate or Path(candidate).is_absolute() or ".." in Path(candidate).parts:
            return fallback
        if candidate.split("/")[0] in {".git", ".agentufo", "node_modules", ".venv"}:
            return fallback
        allowed_suffixes = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".json", ".md", ".yaml", ".yml", ".toml", ".go", ".rs", ".java", ".cpp", ".h", ".sql"}
        try:
            resolved = (self.workspace.root / candidate).resolve(strict=False)
            if not resolved.is_relative_to(self.workspace.root) or resolved.suffix.lower() not in allowed_suffixes:
                return fallback
        except (OSError, ValueError):
            return fallback
        return candidate

    def _scan_workspace_fingerprints(self) -> Dict[str, Tuple[int, int]]:
        """Track bounded source/document files without reading secrets or dependency trees."""
        allowed = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".json", ".md", ".toml", ".yaml", ".yml", ".go", ".rs", ".java", ".cpp", ".h", ".sql"}
        excluded_dirs = {".git", ".agentufo", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", "vendor", "coverage", ".cache", ".next", "site-packages", "third_party"}
        excluded_words = ("secret", "credential", "api_key", "apikey", "token", "password")
        found: Dict[str, Tuple[int, int]] = {}
        visited_dirs = 0
        max_dirs = 600
        max_depth = 5
        try:
            for current, dirs, files in os.walk(self.workspace.root):
                visited_dirs += 1
                depth = len(Path(current).relative_to(self.workspace.root).parts)
                if visited_dirs > max_dirs or depth >= max_depth:
                    dirs[:] = []
                else:
                    dirs[:] = sorted(d for d in dirs if d not in excluded_dirs and not d.startswith("."))[:80]
                for name in files:
                    low = name.lower()
                    if any(word in low for word in excluded_words) or low in {".env", ".env.local", ".env.production"} or low.endswith((".pem", ".p12", ".key", ".secret")):
                        continue
                    if Path(name).suffix.lower() not in allowed:
                        continue
                    path = Path(current) / name
                    try:
                        stat = path.stat()
                        if stat.st_size > 50000:
                            continue
                        rel = path.relative_to(self.workspace.root).as_posix()
                        found[rel] = (stat.st_mtime_ns, stat.st_size)
                    except OSError:
                        continue
                    if len(found) >= 400:
                        return found
        except OSError:
            pass
        return found

    def _workspace_change_context(self) -> str:
        previous = getattr(self, "_workspace_watch_state", None)
        current = self._scan_workspace_fingerprints()
        self._workspace_watch_state = current
        if previous is None:
            return ""
        changed = [p for p, stamp in current.items() if previous.get(p) != stamp]
        removed = [p for p in previous if p not in current]
        if not changed and not removed:
            return ""
        sections = ["LIVE WORKSPACE CHANGE DETECTED. Reconcile this with the task before proceeding."]
        if removed:
            sections.append("Removed files: " + ", ".join(removed[:12]))
        for rel in changed[:8]:
            try:
                content = self.workspace.read_file(rel)[:2400]
                sections.append(f"\n--- changed file: {rel} ---\n{content}")
            except Exception:
                sections.append(f"\n--- changed file: {rel} (could not read latest version) ---")
        return "\n".join(sections)[:10000]

    def _controlled_prompt(self, seat: CrewSeat, prompt: str) -> str:
        """Cooperative per-seat control checkpoint and shared-context injection.

        A provider call already in flight cannot be forcibly interrupted by this synchronous
        provider contract; pause/stop takes effect at the next checkpoint.
        """
        while seat.is_paused and not seat.is_stopped:
            seat.status = "paused"
            self._persist_durable_state()
            time.sleep(0.1)
        if seat.is_stopped:
            raise CrewSeatStopped(f"Seat {seat.seat_id} stopped at a safe checkpoint")
        injected = (seat.injected_context or "").strip()
        if injected:
            seat.injected_context = ""
            self._persist_durable_state()
            prompt += "\n\nLIVE SHARED CONTEXT UPDATE (newest workspace/task information; obey it):\n" + injected
        filesystem_update = self._workspace_change_context()
        if filesystem_update:
            prompt += "\n\n" + filesystem_update
            seat.current_thought = "Reconciling live workspace changes"
            self.post_message("Workspace Watch", seat.seat_id, "filesystem_change", "Live filesystem update", {"summary": filesystem_update[:2000]})
        return prompt

    def run_crew(
        self,
        task: str,
        progress_callback: Optional[Callable[[str, int, Dict[str, Any]], None]] = None,
    ) -> AgentUFOExecutionReport:
        """
        Executes the production-grade AgentUFO Crew Pipeline:
        1. Architect Seat: Plans and defines file paths.
        2. Builder Seat: Locks paths, writes code to real disk, generates tests.
        3. Reviewer Gate: Executes tests in sandbox. Allows 1 repair. Can reject.
        """
        t0 = time.perf_counter()
        if self.mode != AgentUFOMode.CREW_PIPELINE:
            return AgentUFOExecutionReport(
                task=task, mode=self.mode.value, status="error", final_code="",
                target_filepaths=[], seats=self.seats, mailbox_messages=self.mailbox,
                error=f"Execution mode '{self.mode.value}' is not implemented in the verified pipeline. No work was run.",
                elapsed_seconds=time.perf_counter() - t0,
            )

        def emit(msg: str, pct: int, meta: Optional[Dict[str, Any]] = None):
            if progress_callback:
                progress_callback(msg, pct, meta or {})

        architect = self.seats[0]
        builder = self.seats[1]
        reviewer = self.seats[2] if len(self.seats) > 2 else self.seats[1]

        target_file = "solution.py"
        if any(word in task.lower() for word in ("game", "canvas", "html", "web page", "website")):
            target_file = "index.html"
        # Explicit existing paths in the task take precedence over planner suggestions.
        import re
        explicit_path = re.search(r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+[\\/])*[A-Za-z0-9_.-]+\.(?:py|js|mjs|ts|tsx|jsx|html|css|json|md|yaml|yml|toml|go|rs|java|cpp|h|sql)(?![A-Za-z0-9_.-])", task)
        if explicit_path:
            candidate = explicit_path.group(0).replace("\\", "/")
            try:
                resolved = (self.workspace.root / candidate).resolve(strict=False)
                if resolved.is_relative_to(self.workspace.root) and resolved.exists() and resolved.is_file():
                    target_file = candidate
            except (OSError, ValueError):
                pass
        workspace_context = self._workspace_context()
        self._workspace_watch_state = self._scan_workspace_fingerprints()
        emit(f"🚀 AgentUFO Crew activated for task: '{task[:60]}...'", 10, {"status": "started", "workspace_root": str(self.workspace.root)})

        # =====================================================================
        # PHASE 1: Architect Seat (Blueprint & Path Assignment)
        # =====================================================================
        architect.status = "working"
        llm_arch, tier_arch = self._resolve_llm_and_tier(0, architect.seat_id, is_planner=True)
        architect.tier_used = tier_arch

        emit(f"📐 Architect ({architect.seat_id}) designing modular specification...", 25)
        arch_prompt = (
            "You are the Chief Systems Architect for a real repository, not a greenfield demo.\n"
            f"Task: {task}\n"
            f"Fallback target file: {target_file}\n\n"
            f"REAL WORKSPACE CONTEXT:\n{workspace_context}\n\n"
            "Select the smallest appropriate existing file to modify, or one new file if needed. "
            "Use a workspace-relative path, never an absolute path or parent traversal. "
            "Begin your response with exactly `TARGET_FILE: relative/path.ext`, then give the architecture contract, invariants, edge cases, and verification requirements. "
            "Do not choose secrets, dependency trees, build outputs, or AgentUFO runtime state."
        )
        architect.current_thought = "Writing architecture specification"
        resp_arch = self._generate_with_budget(architect, llm_arch, arch_prompt, is_planner=True, is_local_tier=(tier_arch == "local"))
        target_file = self._choose_target_path(task, resp_arch.content, target_file)
        architect.current_thought = f"Selected target {target_file}; handing specification to Builder"
        architect.status = "completed"

        # Architect posts Brief to Mailbox for Builder
        arch_brief = self.post_message(
            from_seat=architect.seat_id,
            to_seat=builder.seat_id,
            msg_type="architectural_brief",
            title=f"Architectural Spec for {target_file}",
            payload={"spec": resp_arch.content, "target_file": target_file},
        )

        # =====================================================================
        # PHASE 2: Builder Seat (Disk Locking & Code Authoring)
        # =====================================================================
        builder.status = "working"
        llm_bld, tier_bld = self._resolve_llm_and_tier(1, builder.seat_id, is_planner=False)
        builder.tier_used = tier_bld

        # Builder acquires real filesystem lock!
        try:
            self.workspace.acquire_lock(target_file, seat_id=builder.seat_id, seat_role="builder")
            builder.assigned_paths = [target_file]
        except PathLockConflictError as e:
            emit(f"❌ Lock Conflict: {e}", 0)
            return AgentUFOExecutionReport(
                task=task, mode=self.mode.value, status="error", final_code="",
                target_filepaths=[], seats=self.seats, mailbox_messages=self.mailbox,
                error=str(e), elapsed_seconds=time.perf_counter() - t0,
            )

        emit(f"🔨 Builder ({builder.seat_id}) acquired lock on '{target_file}' and implementing...", 50)
        try:
            existing_content = self.workspace.read_file(target_file)[:20000]
            file_state = f"EXISTING FILE CONTENT (replace only as required by the task):\n```\n{existing_content}\n```"
        except Exception:
            file_state = "TARGET FILE DOES NOT EXIST YET. Create a complete implementation appropriate for the selected extension."
        bld_prompt = (
            f"You are the Builder. Implement the verified change in the real workspace.\n"
            f"Task: {task}\n"
            f"Target file: {target_file}\n"
            f"Architecture Spec:\n{resp_arch.content}\n\n"
            f"{file_state}\n\n"
            "Preserve unrelated behavior, public interfaces, and project conventions. Add edge-case handling. "
            "Return the complete target file content inside one fenced code block, not a diff and not an explanation."
        )
        builder.current_thought = "Generating implementation"
        resp_bld = self._generate_with_budget(builder, llm_bld, bld_prompt, is_planner=False, is_local_tier=(tier_bld == "local"))

        code_candidate = self._extract_code(resp_bld.content)

        # Stage code in workspace staging area (does NOT pollute repository before review!)
        staged_path = self.workspace.stage_file(
            target_path=target_file,
            content=code_candidate,
            seat_id=builder.seat_id,
        )

        # Generate a tiny fixture only when the project has no discoverable tests.
        # Existing project tests provide stronger coverage and avoid one extra model call.
        test_fixture = ""
        root = self.workspace.root
        has_project_tests = any(root.glob("test_*.py")) or any(root.glob("*_test.py"))
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            try:
                has_project_tests = has_project_tests or any(p.is_file() and (p.name.startswith("test_") or p.name.endswith("_test.py")) for p in list(tests_dir.iterdir())[:200])
            except OSError:
                pass
        if target_file.endswith(".py") and not has_project_tests:
            test_prompt = f"Given this code:\n{code_candidate[:12000]}\n\nWrite 3 pytest or assert statements to verify correctness."
            builder.current_thought = "Generating bounded verification fixture"
            resp_test = self._generate_with_budget(builder, llm_bld, test_prompt, is_planner=False, is_local_tier=(tier_bld == "local"))
            test_fixture = self._extract_code(resp_test.content)
        elif has_project_tests:
            emit("Existing project test suite detected; skipping redundant test-generation model call.", 68, {"status": "testing", "test_fixture": "project_tests"})

        # Builder posts PR to Mailbox for Reviewer Gate
        pr_msg = self.post_message(
            from_seat=builder.seat_id,
            to_seat=reviewer.seat_id,
            msg_type="pull_request",
            title=f"PR: Implementation of {target_file}",
            payload={"code": code_candidate, "staged_path": str(staged_path), "test_fixture": test_fixture},
        )
        builder.status = "waiting_review"

        # =====================================================================
        # PHASE 3: Reviewer Seat (Project Test Execution & Atomic Merge Gate)
        # =====================================================================
        reviewer.status = "working"
        reviewer.current_thought = "Executing tests and deciding merge gate"
        reviewer.calls_made += 1  # one verification pass; not an LLM call
        emit(f"🛡️ Review Gatekeeper ({reviewer.seat_id}) executing project tests & sandbox verification...", 75)

        def repair_callback(broken_code: str, test_err: str) -> str:
            """Builder gets exactly ONE chance to repair code based on test failure."""
            emit(f"⚠️ Test failure detected. Triggering 1 repair cycle for Builder...", 80)
            rep_prompt = (
                f"Your code failed project test verification:\n"
                f"Test Output / Traceback:\n{test_err}\n\n"
                f"Broken Code:\n{broken_code}\n\n"
                f"Fix the bug and output the corrected complete code inside a markdown code block."
            )
            builder.current_thought = "Repairing after failed verification"
            resp_rep = self._generate_with_budget(builder, llm_bld, rep_prompt, is_planner=False, is_local_tier=(tier_bld == "local"))
            repaired = self._extract_code(resp_rep.content)
            # Re-stage repaired candidate
            self.workspace.stage_file(target_file, repaired, seat_id=builder.seat_id)
            return repaired

        # Evaluate merge candidate against sandbox fixtures AND real project tests
        review_report = self.review_gate.evaluate_merge_candidate(
            task=task,
            code_candidate=code_candidate,
            target_file=target_file,
            project_root=self.workspace.root,
            test_fixture=test_fixture,
            repair_fn=repair_callback if target_file.endswith(".py") else None,
        )

        # Post review verdict to Mailbox
        self.post_message(
            from_seat=reviewer.seat_id,
            to_seat=builder.seat_id,
            msg_type="review_verdict",
            title=f"Review Gate Verdict: {review_report.status}",
            payload=review_report.to_dict(),
        )

        final_code = review_report.code_tested or code_candidate

        if review_report.passed:
            # ATOMIC MERGE: Commit staged files to real workspace destination
            self.workspace.commit_staged(target_file, seat_id=builder.seat_id)
            builder.status = "completed"
            reviewer.status = "completed"
            emit(f"✅ Review Gate PASSED ({review_report.status})! Changes verified against tests and merged into repository.", 100)
            status_str = "success"
        else:
            # ATOMIC ROLLBACK / MERGE ABORTED: Discard staged changes, protecting repo
            self.workspace.discard_staged(target_file, seat_id=builder.seat_id)
            builder.status = "rejected"
            reviewer.status = "completed"
            emit(f"⛔ Review Gate REJECTED: Tests failed after 1 repair attempt. Merge aborted; repository untouched.", 100)
            status_str = "rejected"

        # Unlock paths held by builder
        self.workspace.release_all_for_seat(builder.seat_id)
        self._persist_durable_state()

        return AgentUFOExecutionReport(
            task=task,
            mode=self.mode.value,
            status=status_str,
            final_code=final_code,
            target_filepaths=[target_file],
            seats=self.seats,
            mailbox_messages=self.mailbox,
            review_report=review_report,
            budget_telemetry=self.budget_guard.to_dict(),
            elapsed_seconds=time.perf_counter() - t0,
        )

    def _extract_code(self, raw: str) -> str:
        """Extracts code from markdown fences."""
        import re
        if "```" in raw:
            matches = re.findall(r"```(?:[a-zA-Z0-9_\-]+)?\s*([\s\S]*?)\s*```", raw)
            if matches:
                return matches[0].strip()
        return raw.strip()
