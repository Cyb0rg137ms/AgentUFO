"""
budget_guard.py
===============
Hard Token & Dollar Budget Enforcement System.

Enforces pre-run hard budgets:
- Global run budget cap
- Planner token cap
- Per-seat token cap
- Immediate stop when budget is reached
- Zero silent fallback to paid cloud API when local runtime is unavailable!
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


class BudgetCapExceededError(Exception):
    """Raised when a crew seat or the entire run exceeds its hard token budget."""

    pass


class LocalRuntimeUnavailableError(Exception):
    """
    Raised when a seat configured for token-saving cannot reach the local runtime (Ollama).
    Prevents silent fallback that burns the user's paid cloud API tokens.
    """

    pass


@dataclass
class BudgetConfig:
    """Pre-run hard budget limits."""

    total_token_cap: int = 50_000
    planner_token_cap: int = 10_000
    per_seat_token_cap: int = 15_000
    max_usd_cap: float = 0.50
    strict_no_cloud_fallback: bool = True  # Never silently burn cloud credits if local fails
    max_total_tokens: Optional[int] = None
    max_planner_tokens: Optional[int] = None
    total_spend_limit_usd: Optional[float] = None

    def __post_init__(self) -> None:
        if self.max_total_tokens is not None:
            self.total_token_cap = self.max_total_tokens
        if self.max_planner_tokens is not None:
            self.planner_token_cap = self.max_planner_tokens
        if self.total_spend_limit_usd is not None:
            self.max_usd_cap = self.total_spend_limit_usd

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_token_cap": self.total_token_cap,
            "planner_token_cap": self.planner_token_cap,
            "per_seat_token_cap": self.per_seat_token_cap,
            "max_usd_cap": self.max_usd_cap,
            "strict_no_cloud_fallback": self.strict_no_cloud_fallback,
        }


class HardBudgetGuard:
    """
    Monitors and enforces hard limits on token expenditure across all seats.
    """

    def __init__(self, config: Optional[BudgetConfig] = None) -> None:
        self.config = config or BudgetConfig()
        self.total_tokens_used: int = 0
        self.planner_tokens_used: int = 0
        self.seat_tokens_used: Dict[str, int] = {}
        self.seat_calls: Dict[str, int] = {}
        self.total_cost_usd: float = 0.0
        self.is_halted: bool = False
        self.halt_reason: Optional[str] = None

    def check_headroom(
        self,
        seat_id: str,
        is_planner: bool = False,
        estimated_tokens: int = 300,
        is_local_tier: bool = False,
    ) -> None:
        """
        Validates whether sufficient budget remains before initiating a model call.
        Raises BudgetCapExceededError immediately if caps are breached.
        """
        if self.is_halted:
            raise BudgetCapExceededError(
                f"Run is halted due to budget exhaustion: {self.halt_reason}"
            )

        # 1. Total run budget check
        if (self.total_tokens_used + estimated_tokens) > self.config.total_token_cap:
            self.is_halted = True
            self.halt_reason = f"Total token budget exceeded: {self.total_tokens_used} / {self.config.total_token_cap}"
            raise BudgetCapExceededError(self.halt_reason)

        # 2. Planner cap check
        if (
            is_planner
            and (self.planner_tokens_used + estimated_tokens) > self.config.planner_token_cap
        ):
            self.is_halted = True
            self.halt_reason = f"Planner token cap exceeded: {self.planner_tokens_used} / {self.config.planner_token_cap}"
            raise BudgetCapExceededError(self.halt_reason)

        # 3. Per-seat cap check
        current_seat_tok = self.seat_tokens_used.get(seat_id, 0)
        if (current_seat_tok + estimated_tokens) > self.config.per_seat_token_cap:
            self.is_halted = True
            self.halt_reason = f"Seat '{seat_id}' token cap exceeded: {current_seat_tok} / {self.config.per_seat_token_cap}"
            raise BudgetCapExceededError(self.halt_reason)

        # 4. Dollar cap check. Local inference has no cloud spend, so a $0 cap is valid
        # for local-only runs while it blocks any cloud call before that call is issued.
        if not is_local_tier and self.total_cost_usd >= self.config.max_usd_cap:
            self.is_halted = True
            self.halt_reason = f"Estimated cloud spend cap reached: ${self.total_cost_usd:.4f} / ${self.config.max_usd_cap:.2f}"
            raise BudgetCapExceededError(self.halt_reason)

    def record_usage(
        self,
        seat_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        is_planner: bool = False,
        is_local_tier: bool = False,
        cost_per_1k: float = 0.003,
    ) -> None:
        """Records token and financial consumption for a seat."""
        tokens = prompt_tokens + completion_tokens
        self.total_tokens_used += tokens

        if is_planner:
            self.planner_tokens_used += tokens

        self.seat_tokens_used[seat_id] = self.seat_tokens_used.get(seat_id, 0) + tokens
        self.seat_calls[seat_id] = self.seat_calls.get(seat_id, 0) + 1

        # Local LLM calls cost $0.00 in cloud fees!
        if not is_local_tier:
            call_cost = (tokens / 1000.0) * cost_per_1k
            self.total_cost_usd += round(call_cost, 6)

    def verify_local_runtime_or_fail(self, seat_id: str, is_available: bool) -> None:
        """
        Ensures local runtime is live.
        If strict mode is enabled and local is down, halts rather than silently burning cloud tokens!
        """
        if not is_available and self.config.strict_no_cloud_fallback:
            self.is_halted = True
            self.halt_reason = (
                f"Seat '{seat_id}' configured for Save Tokens, but local Ollama runtime is offline. "
                f"Aborting call to protect cloud token budget. (Strict fallback prevention enabled)"
            )
            raise LocalRuntimeUnavailableError(self.halt_reason)

    def to_dict(self) -> Dict[str, Any]:
        pct_used = round((self.total_tokens_used / max(1, self.config.total_token_cap)) * 100.0, 1)
        return {
            "total_tokens_used": self.total_tokens_used,
            "total_token_cap": self.config.total_token_cap,
            "percentage_used": pct_used,
            "planner_tokens_used": self.planner_tokens_used,
            "planner_token_cap": self.config.planner_token_cap,
            "per_seat_token_cap": self.config.per_seat_token_cap,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "total_estimated_usd": round(self.total_cost_usd, 4),
            "max_usd_cap": self.config.max_usd_cap,
            "seat_breakdown": self.seat_tokens_used,
            "is_halted": self.is_halted,
            "halt_reason": self.halt_reason,
        }

    get_summary = to_dict
