"""Durable task-run orchestration for the AgentUFO control room.

The run ledger is persisted atomically to .agentufo/runtime/runs.json. Model calls
remain owned by the existing coordinator; this module adds a durable control plane
and never fabricates model output or token usage.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from agent_core.budget_guard import BudgetConfig, HardBudgetGuard


class CrewRunManager:
    ACTIVE = {"queued", "planning", "running", "pause_requested", "paused", "stop_requested"}

    def __init__(self, session_manager: Any) -> None:
        self.session_manager = session_manager
        self.root = Path(__file__).resolve().parent.parent / ".agentufo" / "runtime"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "runs.json"
        self._lock = threading.RLock()
        self._last_persist_monotonic = 0.0
        self._pause: Dict[str, threading.Event] = {}
        self._stop: Dict[str, threading.Event] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._runs: Dict[str, Dict[str, Any]] = self._load()
        # Mark work interrupted by a process crash explicitly; never pretend it completed.
        changed = False
        for run in self._runs.values():
            if run.get("status") in self.ACTIVE:
                run["status"] = "interrupted"
                run["error"] = (
                    "The server restarted before this run could be confirmed complete. Restart it explicitly."
                )
                run["updated_at"] = time.time()
                changed = True
        if changed:
            self._persist()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _persist(self) -> None:
        temp = self.state_path.with_suffix(".tmp")
        # Compact JSON and a bounded recent-run ledger reduce disk churn and memory spikes.
        if len(self._runs) > 40:
            keep = sorted(
                self._runs.items(), key=lambda item: item[1].get("created_at", 0), reverse=True
            )
            active = [(k, v) for k, v in keep if v.get("status") in self.ACTIVE]
            recent = [(k, v) for k, v in keep if v.get("status") not in self.ACTIVE][
                : max(0, 40 - len(active))
            ]
            self._runs = dict(active + recent)
        temp.write_text(
            json.dumps(self._runs, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        os.replace(temp, self.state_path)
        self._last_persist_monotonic = time.monotonic()

    def _event(
        self, run: Dict[str, Any], kind: str, message: str, details: Optional[Dict[str, Any]] = None
    ) -> None:
        events = run.setdefault("events", [])
        events.append(
            {
                "seq": len(events) + 1,
                "at": time.time(),
                "kind": kind,
                "message": str(message)[:2000],
                "details": details or {},
            }
        )
        # Bounded history keeps the state file predictable during long sessions.
        if len(events) > 250:
            run["events"] = events[-250:]
        run["updated_at"] = time.time()
        # Progress is high-frequency telemetry. Persist it at most every 1.5s;
        # lifecycle transitions and errors are always flushed immediately.
        now_mono = time.monotonic()
        if kind != "progress" or now_mono - self._last_persist_monotonic >= 1.5:
            self._persist()

    def list_runs(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "runs": [
                    self._public(r)
                    for r in sorted(
                        self._runs.values(), key=lambda x: x.get("created_at", 0), reverse=True
                    )[:50]
                ]
            }

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            run = self._runs.get(run_id)
            return self._public(run) if run else None

    def events(self, run_id: str, after: int = 0) -> Dict[str, Any]:
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return {"error": "Run not found"}
            return {
                "run_id": run_id,
                "events": [e for e in run.get("events", []) if e.get("seq", 0) > after],
                "last_seq": len(run.get("events", [])),
            }

    @staticmethod
    def _public(run: Dict[str, Any]) -> Dict[str, Any]:
        # Copy JSON-safe fields; no credentials or provider objects are stored in runs.
        return json.loads(json.dumps(run))

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return {"status": "error", "error": "Task cannot be empty."}
        requested_mode = str(payload.get("mode") or "crew_pipeline")
        if requested_mode != "crew_pipeline":
            return {
                "status": "error",
                "error": f"Mode '{requested_mode}' is not enabled in this verified build. Only Architect → Builder → Reviewer Gate is available; unsupported modes are rejected instead of silently running a different strategy.",
            }
        if len(prompt) > 30000:
            return {"status": "error", "error": "Task must be at most 30,000 characters."}
        n = 3  # The verified pipeline has three roles: architect, builder, reviewer.
        with self._lock:
            active = [r for r in self._runs.values() if r.get("status") in self.ACTIVE]
            if active:
                return {
                    "status": "error",
                    "error": "AgentUFO's coordinator is stateful; wait for the active run to finish or stop it before starting another run.",
                    "active_run_id": active[0].get("id"),
                }
        selected_models = payload.get("selected_models", [])
        if not isinstance(selected_models, list):
            return {
                "status": "error",
                "error": "selected_models must be a list of provider/model objects.",
            }
        if len(selected_models) > n:
            return {
                "status": "error",
                "error": "This verified pipeline has three active seats; select at most three models.",
            }
        if any(
            not isinstance(m, dict)
            or not str(m.get("provider") or "").strip()
            or not str(m.get("model") or "").strip()
            for m in selected_models
        ):
            return {
                "status": "error",
                "error": "Every selected model must include a provider and model name.",
            }
        try:
            budget_usd = max(0.0, min(1000.0, float(payload.get("budget_usd", 2.0) or 0)))
        except (ValueError, TypeError):
            return {"status": "error", "error": "budget_usd must be a number between 0 and 1000."}
        try:
            token_cap = max(1, min(1_000_000, int(payload.get("token_cap", 30000) or 30000)))
            planner_token_cap = max(
                1, min(token_cap, int(payload.get("planner_token_cap", 5000) or 5000))
            )
        except (ValueError, TypeError):
            return {"status": "error", "error": "Token caps must be positive integers."}
        run_id = "run-" + uuid.uuid4().hex[:12]
        now = time.time()
        seats = []
        presets = [
            ("seat_1_architect", "Architect"),
            ("seat_2_builder", "Builder"),
            ("seat_3_reviewer", "Reviewer"),
            ("seat_4_refactorer", "Refactorer"),
            ("seat_5_security", "Security"),
        ]
        for i in range(n):
            seat_id, name = (
                presets[i] if i < len(presets) else (f"seat_{i + 1}_worker", f"Worker {i + 1}")
            )
            seats.append(
                {
                    "seat_id": seat_id,
                    "name": name,
                    "status": "queued",
                    "tokens": 0,
                    "tokens_estimated": True,
                    "model": "assigned by coordinator",
                    "progress": 0,
                    "last_event": "Queued",
                }
            )
        run = {
            "id": run_id,
            "prompt": prompt,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "mode": payload.get("mode", "crew_pipeline"),
            "agents": n,
            "token_saving": bool(payload.get("token_saving", True)),
            "selected_models": selected_models,
            "budget_usd": budget_usd,
            "token_cap": token_cap,
            "planner_token_cap": planner_token_cap,
            "tokens": 0,
            "tokens_estimated": True,
            "seats": seats,
            "events": [],
            "result": None,
            "error": None,
            "pause_requested": False,
            "stop_requested": False,
        }
        with self._lock:
            active = [r for r in self._runs.values() if r.get("status") in self.ACTIVE]
            if active:
                return {
                    "status": "error",
                    "error": "Another crew run started concurrently; wait for it to finish.",
                    "active_run_id": active[0].get("id"),
                }
            self._runs[run_id] = run
            self._pause[run_id] = threading.Event()
            self._pause[run_id].set()
            self._stop[run_id] = threading.Event()
            self._event(run, "queued", "Run queued", {"agents": n, "mode": run["mode"]})
            t = threading.Thread(
                target=self._execute, args=(run_id,), name=f"agentufo-{run_id}", daemon=True
            )
            self._threads[run_id] = t
            t.start()
        return {"status": "accepted", "run": self.get_run(run_id)}

    def _cleanup_interrupted_workspace(self) -> None:
        """Release builder locks and discard unmerged staged candidates after a stop/error."""
        workspace = getattr(self.session_manager, "workspace_mgr", None)
        if workspace is None:
            return
        try:
            for target in ("solution.py", "index.html"):
                try:
                    workspace.discard_staged(target, seat_id="seat_2_builder")
                except Exception:
                    pass
            workspace.release_all_for_seat("seat_2_builder")
        except Exception:
            pass

    def _execute(self, run_id: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return
            run["status"] = "planning"
            for seat in run["seats"]:
                seat["status"] = "planning"
            self._event(run, "planning", "Coordinator is preparing the crew")
            prompt = run["prompt"]
            mode = run["mode"]
            agents = run["agents"]
            token_saving = run["token_saving"]
        last_text_tokens = 0
        telemetry_cache: Dict[str, Any] = {"at": 0.0, "by_id": {}}

        def progress(
            evt: Any, pct: Optional[int] = None, meta: Optional[Dict[str, Any]] = None
        ) -> None:
            nonlocal last_text_tokens
            pause_event = self._pause[run_id]
            stop_event = self._stop[run_id]
            if stop_event.is_set():
                raise _StopRequested("Stop requested by user")
            pause_event.wait()
            if stop_event.is_set():
                raise _StopRequested("Stop requested by user")
            data = (
                evt.to_dict()
                if hasattr(evt, "to_dict")
                else (evt if isinstance(evt, dict) else {"message": str(evt)})
            )
            if not isinstance(data, dict):
                data = {"message": str(data)}
            if pct is not None:
                data.setdefault("progress_pct", pct)
            if meta:
                data.update(meta)
            message = str(
                data.get("message")
                or data.get("detail")
                or data.get("stage")
                or data.get("event")
                or evt
                or "Agent activity"
            )
            raw_tokens = data.get("total_tokens") or data.get("tokens")
            with self._lock:
                run = self._runs.get(run_id)
                if not run:
                    return
                run["status"] = "running"
                if isinstance(raw_tokens, (int, float)):
                    run["tokens"] = max(run.get("tokens", 0), int(raw_tokens))
                    run["tokens_estimated"] = False
                else:
                    # Progress text is not provider token telemetry. Show an explicitly labelled estimate.
                    last_text_tokens += max(1, len(message) // 4)
                    run["tokens"] = max(run.get("tokens", 0), last_text_tokens)
                    run["tokens_estimated"] = True
                if run.get("token_cap", 0) and run["tokens"] >= run["token_cap"]:
                    self._stop[run_id].set()
                    run["status"] = "stop_requested"
                    run["error"] = "Token budget reached; requesting a cooperative stop."
                    self._event(
                        run,
                        "budget_stop",
                        run["error"],
                        {
                            "token_cap": run["token_cap"],
                            "observed_tokens": run["tokens"],
                            "estimated": run["tokens_estimated"],
                        },
                    )
                    raise _BudgetExceeded(run["error"])
                # Merge actual coordinator seat telemetry when available; don't paint every seat as equally active.
                try:
                    now_mono = time.monotonic()
                    if now_mono - telemetry_cache["at"] >= 0.8:
                        telemetry = self.session_manager.get_agentufo_cockpit()
                        telemetry_cache["by_id"] = {
                            str(item.get("seat_id")): item for item in telemetry.get("seats", [])
                        }
                        telemetry_cache["at"] = now_mono
                    live_by_id = telemetry_cache["by_id"]
                except Exception:
                    live_by_id = {}
                for seat in run["seats"]:
                    live = live_by_id.get(seat["seat_id"], {})
                    live_status = str(live.get("status") or "")
                    if live_status:
                        seat["status"] = live_status
                    elif seat["status"] in ("queued", "planning"):
                        seat["status"] = "running"
                    seat["progress"] = max(
                        int(seat.get("progress", 0)), int(data.get("progress_pct", 0) or 0)
                    )
                    seat["last_event"] = str(live.get("current_thought") or message)[:180]
                    if live.get("assigned_model") or live.get("assigned_provider"):
                        seat["model"] = (
                            f"{live.get('assigned_provider') or ''} / {live.get('assigned_model') or ''}".strip(
                                " /"
                            )
                        )
                    actual_tokens = live.get("tokens_consumed")
                    if isinstance(actual_tokens, (int, float)):
                        seat["tokens"] = int(actual_tokens)
                        seat["tokens_estimated"] = False
                    elif isinstance(live.get("live_tokens"), (int, float)) and live.get(
                        "live_tokens"
                    ):
                        seat["tokens"] = int(live["live_tokens"])
                        seat["tokens_estimated"] = False
                    else:
                        seat["tokens"] = max(
                            seat.get("tokens", 0), run["tokens"] // max(1, len(run["seats"]))
                        )
                        seat["tokens_estimated"] = run["tokens_estimated"]
                self._event(run, "progress", message, data)

        try:
            with self._lock:
                run = self._runs[run_id]
                # Every run gets a fresh budget ledger; previous missions must not consume this run's cap.
                fresh_guard = HardBudgetGuard(
                    BudgetConfig(
                        total_token_cap=run["token_cap"],
                        planner_token_cap=run["planner_token_cap"],
                        per_seat_token_cap=max(1, run["token_cap"] // max(1, agents)),
                        max_usd_cap=run["budget_usd"],
                        strict_no_cloud_fallback=True,
                    )
                )
                self.session_manager.budget_guard = fresh_guard
                if hasattr(self.session_manager, "agentufo_coordinator"):
                    self.session_manager.agentufo_coordinator.budget_guard = fresh_guard
                # Clear previous seat bindings before applying this run's explicit fleet.
                self.session_manager.seat_model_bindings = {}
                for old_seat in getattr(self.session_manager.agentufo_coordinator, "seats", []):
                    old_seat.assigned_provider = None
                    old_seat.assigned_model = None
                selected = run.get("selected_models") or []
                roles = ["Architect", "Builder", "Reviewer", "QA / Worker"]
                fleet = [
                    {
                        "id": f"{choice.get('provider', 'provider')}-{choice.get('model', 'model')}",
                        "provider": choice.get("provider"),
                        "model": choice.get("model"),
                        "name": f"{choice.get('provider')} / {choice.get('model')}",
                        "role": roles[i] if i < len(roles) else f"Worker {i + 1}",
                        "active": True,
                    }
                    for i, choice in enumerate(selected)
                    if isinstance(choice, dict)
                ]
                # Apply selected crew settings and fleet to the actual coordinator, not just the UI.
                self.session_manager.configure_agentufo(
                    {
                        "mode": mode,
                        "num_seats": agents,
                        "token_policy": "all" if token_saving else "cloud_only",
                        "multi_llm_fleet": fleet,
                        "budget": {
                            "max_usd_cap": run["budget_usd"],
                            "total_token_cap": run["token_cap"],
                            "per_seat_token_cap": max(1, run["token_cap"] // max(1, agents)),
                            "planner_token_cap": run["planner_token_cap"],
                        },
                    }
                )
                bindings = {}
                for choice in selected:
                    if (
                        isinstance(choice, dict)
                        and str(choice.get("provider") or "").lower() == "ollama"
                        and choice.get("model")
                    ):
                        model_name = str(choice["model"])
                        if not self.session_manager.capability_prober.get_profile(model_name):
                            self._event(
                                run,
                                "model_probe",
                                f"Running first-use capability probe for local model {model_name}",
                            )
                            self.session_manager.capability_prober.probe_model(model_name)
                current_seats = self.session_manager.agentufo_coordinator.seats
                for i, choice in enumerate(selected):
                    if i >= len(current_seats) or not isinstance(choice, dict):
                        continue
                    provider = str(choice.get("provider") or "").strip().lower()
                    model = str(choice.get("model") or "").strip()
                    if provider and model:
                        bindings[current_seats[i].seat_id] = {"provider": provider, "model": model}
                if bindings:
                    self.session_manager.configure_agentufo({"seat_models": bindings})
                run["status"] = "running"
                self._event(
                    run,
                    "started",
                    "Crew execution started",
                    {"model_count": len(selected), "token_saving": token_saving},
                )
            result = self.session_manager.run_agentufo_mission(prompt, progress_callback=progress)
            with self._lock:
                run = self._runs[run_id]
                if self._stop[run_id].is_set():
                    run["status"] = "stopped"
                    self._event(run, "stopped", "Stop requested; result was not marked complete")
                else:
                    run["result"] = result
                    seat_usage = (
                        result.get("seats") if isinstance(result.get("seats"), list) else []
                    )
                    observed_tokens = sum(
                        int(item.get("tokens_consumed", 0) or 0)
                        for item in seat_usage
                        if isinstance(item, dict)
                    )
                    run["tokens"] = int(
                        result.get("total_tokens_used")
                        or result.get("effective_tokens")
                        or observed_tokens
                        or run.get("tokens", 0)
                    )
                    usage_complete = bool(seat_usage) and all(
                        isinstance(item, dict) and not item.get("token_usage_estimated", True)
                        for item in seat_usage
                    )
                    run["tokens_estimated"] = not bool(
                        result.get("token_usage_complete", False) or usage_complete
                    )
                    # Normalize crew report fields for the UI without discarding the raw report.
                    result.setdefault("code", result.get("final_code", ""))
                    review = result.get("review") or {}
                    result.setdefault("test_logs", review.get("final_test_output", ""))
                    result.setdefault(
                        "content",
                        result.get("error")
                        or f"Review: {review.get('status', 'not reported')} · files: {', '.join(result.get('target_filepaths', []))}",
                    )
                    status = str(result.get("status") or "completed").lower()
                    run["status"] = (
                        "completed"
                        if status in ("success", "completed", "passed")
                        else ("budget_exceeded" if status == "budget_exceeded" else "failed_review")
                    )
                    for seat in run["seats"]:
                        seat["status"] = (
                            "completed" if run["status"] == "completed" else "review_failed"
                        )
                        seat["progress"] = (
                            100
                            if run["status"] == "completed"
                            else min(95, seat.get("progress", 70))
                        )
                        seat["last_event"] = "Result returned; see verification report"
                    self._event(
                        run,
                        run["status"],
                        "Crew returned a result",
                        {
                            "verification_level": result.get("verification_level"),
                            "token_usage_complete": result.get("token_usage_complete", False),
                        },
                    )
        except _BudgetExceeded as exc:
            with self._lock:
                run = self._runs.get(run_id)
                if run:
                    run["status"] = "budget_exceeded"
                    run["error"] = str(exc)
                    for seat in run["seats"]:
                        if seat["status"] not in ("completed", "review_failed"):
                            seat["status"] = "stopped_budget"
                    self._event(run, "budget_exceeded", str(exc))
        except _StopRequested as exc:
            self._cleanup_interrupted_workspace()
            with self._lock:
                run = self._runs.get(run_id)
                if run:
                    run["status"] = "stopped"
                    run["error"] = str(exc)
                    for seat in run["seats"]:
                        if seat["status"] not in ("completed", "review_failed"):
                            seat["status"] = "stopped"
                    self._event(run, "stopped", str(exc))
        except Exception as exc:
            stopped = type(exc).__name__ in {"CrewSeatStopped", "_StopRequested"}
            self._cleanup_interrupted_workspace()
            with self._lock:
                run = self._runs.get(run_id)
                if run:
                    run["status"] = "stopped" if stopped else "failed"
                    run["error"] = f"{type(exc).__name__}: {str(exc)[:1000]}"
                    for seat in run["seats"]:
                        if seat["status"] not in ("completed", "review_failed"):
                            seat["status"] = "stopped" if stopped else "failed"
                    self._event(run, "stopped" if stopped else "error", run["error"])

    def control(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(payload.get("run_id") or "")
        action = str(payload.get("action") or "")
        seat_id = str(payload.get("seat_id") or "")
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return {"status": "error", "error": "Run not found"}
            if seat_id and action in ("pause", "resume", "stop", "edit"):
                context = str(payload.get("prompt") or payload.get("context") or "")
                seat_result = self.session_manager.execute_seat_action(seat_id, action, context)
                if not seat_result.get("success"):
                    return {
                        "status": "error",
                        "error": f"Seat action '{action}' was rejected for {seat_id}.",
                        "seat_id": seat_id,
                    }
                for s in run["seats"]:
                    if s["seat_id"] == seat_id:
                        s["status"] = {
                            "pause": "paused",
                            "resume": "running",
                            "stop": "stopped",
                            "edit": s["status"],
                        }[action]
                        if action == "edit":
                            s["last_event"] = "Shared context injected"
                self._event(
                    run,
                    f"seat_{action}",
                    f"{seat_id}: {action}",
                    {"seat_id": seat_id, "context": context[:1000] if action == "edit" else None},
                )
                self._persist()
                return {"status": "ok", "run": self._public(run), "seat_action": seat_result}
            if action == "pause":
                self._pause[run_id].clear()
                run["status"] = "pause_requested"
                run["pause_requested"] = True
                for s in run["seats"]:
                    if not seat_id or s["seat_id"] == seat_id:
                        s["status"] = "pause_requested"
                self._event(
                    run,
                    "pause_requested",
                    "Pause requested; an in-flight provider request may finish before control takes effect",
                    {"seat_id": seat_id or None},
                )
            elif action == "resume":
                self._pause[run_id].set()
                run["pause_requested"] = False
                if run["status"] in ("pause_requested", "paused"):
                    run["status"] = "running"
                for s in run["seats"]:
                    if (not seat_id or s["seat_id"] == seat_id) and s["status"] in (
                        "pause_requested",
                        "paused",
                    ):
                        s["status"] = "running"
                self._event(run, "resumed", "Run resumed", {"seat_id": seat_id or None})
            elif action == "stop":
                self._stop[run_id].set()
                run["stop_requested"] = True
                run["status"] = "stop_requested"
                self._event(
                    run,
                    "stop_requested",
                    "Stop requested; provider calls that are already in flight may not be interruptible",
                )
            elif action == "edit":
                new_prompt = str(payload.get("prompt") or "").strip()
                if not new_prompt:
                    return {"status": "error", "error": "Edited instruction cannot be empty"}
                run["prompt"] = new_prompt
                # Explicit shared change event; all seats can observe it in their task timeline.
                for s in run["seats"]:
                    s["last_event"] = "Shared instruction changed; injected into seat memory"
                    self.session_manager.execute_seat_action(
                        s["seat_id"],
                        "edit",
                        f"Shared live instruction update for run {run_id}:\n{new_prompt}",
                    )
                self._event(
                    run,
                    "instruction_changed",
                    "Shared task instruction edited and injected into each seat's durable context; an already-issued provider request cannot be rewritten",
                    {"prompt": new_prompt[:4000]},
                )
            elif action == "restart":
                prompt = str(payload.get("prompt") or run.get("prompt") or "")
                cloned = {
                    "prompt": prompt,
                    "agents": run["agents"],
                    "mode": run["mode"],
                    "token_saving": run["token_saving"],
                    "selected_models": run.get("selected_models", []),
                    "budget_usd": run.get("budget_usd", 2.0),
                    "token_cap": run.get("token_cap", 30000),
                    "planner_token_cap": run.get("planner_token_cap", 5000),
                }
                return self.create(cloned)
            else:
                return {"status": "error", "error": "Unsupported action"}
            self._persist()
            return {"status": "ok", "run": self._public(run)}


class _StopRequested(Exception):
    pass


class _BudgetExceeded(Exception):
    pass
