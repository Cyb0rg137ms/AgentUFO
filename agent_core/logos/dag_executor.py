"""Dependency-aware concurrent execution with fail-closed DAG validation.

Independent ready nodes run concurrently; dependent nodes become eligible as soon as
all dependencies finish. Invalid graphs never execute any work.
"""
from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


@dataclass
class DAGTaskNode:
    id: str
    name: str
    payload: Any
    dependencies: Set[str] = field(default_factory=set)
    dependents: Set[str] = field(default_factory=set)
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    layer: int = 1
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "dependencies": sorted(self.dependencies),
                "dependents": sorted(self.dependents), "status": self.status.value,
                "layer": self.layer, "has_result": self.result is not None, "error": self.error,
                "started_at": self.started_at, "finished_at": self.finished_at}


class DAGValidationError(ValueError):
    """The graph contains unknown dependencies, a cycle, or duplicate task IDs."""


class DependencyDAG:
    def __init__(self) -> None:
        self.nodes: Dict[str, DAGTaskNode] = {}

    def add_task(self, task_id: str, name: str, payload: Any,
                 dependencies: Optional[List[str]] = None, layer: int = 1) -> DAGTaskNode:
        if not task_id or task_id in self.nodes:
            raise DAGValidationError(f"Task id must be non-empty and unique: {task_id!r}")
        deps = set(dependencies or [])
        if task_id in deps:
            raise DAGValidationError(f"Task {task_id!r} cannot depend on itself")
        node = DAGTaskNode(id=task_id, name=name, payload=payload, dependencies=deps, layer=layer)
        self.nodes[task_id] = node
        for dep_id in deps:
            if dep_id in self.nodes:
                self.nodes[dep_id].dependents.add(task_id)
        for other_id, other in self.nodes.items():
            if task_id in other.dependencies:
                node.dependents.add(other_id)
        return node

    def validate(self) -> List[List[DAGTaskNode]]:
        unknown = {dep for n in self.nodes.values() for dep in n.dependencies if dep not in self.nodes}
        if unknown:
            raise DAGValidationError("Unknown dependency IDs: " + ", ".join(sorted(unknown)))
        degree = {nid: len(node.dependencies) for nid, node in self.nodes.items()}
        ready = sorted(nid for nid, d in degree.items() if d == 0)
        waves: List[List[DAGTaskNode]] = []
        processed: Set[str] = set()
        while ready:
            wave_ids = ready
            waves.append([self.nodes[nid] for nid in wave_ids])
            next_ready: List[str] = []
            for nid in wave_ids:
                processed.add(nid)
                for child in sorted(self.nodes[nid].dependents):
                    degree[child] -= 1
                    if degree[child] == 0:
                        next_ready.append(child)
            ready = sorted(next_ready)
        if len(processed) != len(self.nodes):
            cyclic = sorted(set(self.nodes) - processed)
            raise DAGValidationError("Dependency cycle detected involving: " + ", ".join(cyclic))
        return waves

    def get_ready_tasks(self) -> List[DAGTaskNode]:
        ready = []
        for node in self.nodes.values():
            if node.status == TaskStatus.PENDING and all(
                self.nodes[d].status == TaskStatus.COMPLETED for d in node.dependencies
            ):
                node.status = TaskStatus.READY
                ready.append(node)
        return ready

    def mark_running(self, task_id: str) -> None:
        node = self.nodes[task_id]
        node.status, node.started_at = TaskStatus.RUNNING, time.time()

    def mark_completed(self, task_id: str, result: Any) -> None:
        node = self.nodes[task_id]
        node.status, node.result, node.finished_at = TaskStatus.COMPLETED, result, time.time()

    def mark_failed(self, task_id: str, error: str) -> None:
        node = self.nodes[task_id]
        node.status, node.error, node.finished_at = TaskStatus.FAILED, error[:4000], time.time()

    def is_finished(self) -> bool:
        return all(n.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED)
                   for n in self.nodes.values())

    def compute_antichains(self) -> List[List[DAGTaskNode]]:
        return self.validate()


class AntichainParallelExecutor:
    """Run DAG nodes concurrently up to ``max_workers`` with dependency-aware scheduling."""
    def __init__(self, max_workers: int = 4, trace: Optional[Any] = None) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self.max_workers = max_workers
        self.trace = trace

    def _emit(self, event: str, node: Optional[DAGTaskNode] = None, **fields: Any) -> None:
        if self.trace is not None:
            try:
                self.trace.emit(event, task_id=node.id if node else None, fields=fields)
            except Exception:
                pass  # Trace storage failure must not corrupt scheduler state.

    def execute_dag(self, dag: DependencyDAG, worker_fn: Callable[[DAGTaskNode], Any],
                    progress_callback: Optional[Callable[[DAGTaskNode, int, int], None]] = None
                    ) -> Dict[str, Any]:
        # Validate before running a single task; do not silently break cycles.
        dag.validate()
        total = len(dag.nodes)
        completed = 0
        results: Dict[str, Any] = {}
        running: Dict[concurrent.futures.Future[Any], DAGTaskNode] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers,
                                                   thread_name_prefix="agentufo-worker") as pool:
            while True:
                # A failed prerequisite blocks all descendants rather than executing them.
                changed = True
                while changed:
                    changed = False
                    for node in dag.nodes.values():
                        if node.status == TaskStatus.PENDING and any(
                            dag.nodes[d].status in (TaskStatus.FAILED, TaskStatus.BLOCKED)
                            for d in node.dependencies
                        ):
                            node.status = TaskStatus.BLOCKED
                            node.error = "Blocked because a dependency failed or was blocked."
                            node.finished_at = time.time()
                            self._emit("task.blocked", node, error=node.error)
                            results[node.id] = None
                            completed += 1
                            changed = True
                ready_nodes = dag.get_ready_tasks()
                for index, node in enumerate(ready_nodes):
                    if len(running) >= self.max_workers:
                        for deferred in ready_nodes[index:]:
                            deferred.status = TaskStatus.PENDING
                        break
                    dag.mark_running(node.id)
                    self._emit("task.started", node, dependencies=sorted(node.dependencies))
                    running[pool.submit(worker_fn, node)] = node
                if not running:
                    if all(n.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED)
                           for n in dag.nodes.values()):
                        break
                    # This should only be reachable if graph state was externally corrupted.
                    pending = [n.id for n in dag.nodes.values() if n.status == TaskStatus.PENDING]
                    raise DAGValidationError("Scheduler deadlock; pending tasks: " + ", ".join(pending))
                done, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    node = running.pop(future)
                    completed += 1
                    try:
                        result = future.result()
                        dag.mark_completed(node.id, result)
                        self._emit("task.completed", node, duration_ms=round((node.finished_at - node.started_at) * 1000, 3))
                        results[node.id] = result
                    except Exception as exc:
                        dag.mark_failed(node.id, f"{type(exc).__name__}: {exc}")
                        self._emit("task.failed", node, error=node.error,
                                   duration_ms=round((node.finished_at - node.started_at) * 1000, 3))
                        results[node.id] = None
                    if progress_callback:
                        try:
                            progress_callback(node, completed, total)
                        except Exception:
                            # Telemetry must not turn successful work into a failed task.
                            pass
        return results
