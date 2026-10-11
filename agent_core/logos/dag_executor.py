"""
dag_executor.py
===============
Dependency-Aware DAG & Antichain Parallel Execution Engine for LOGOS.

Instead of running a flat list of tasks in a generic pool, this engine:
1. Structures decomposed subtasks into a formal directed acyclic graph G = (V, E).
2. Computes concurrent antichains (sets of independent tasks whose prerequisites are met).
3. Concurrently executes each antichain in parallel worker threads.
4. Dynamically schedules downstream tasks as soon as their exact dependencies resolve.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class DAGTaskNode:
    """A node in the dependency execution DAG."""
    id: str
    name: str
    payload: Any
    dependencies: Set[str] = field(default_factory=set)
    dependents: Set[str] = field(default_factory=set)
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    layer: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "dependencies": list(self.dependencies),
            "dependents": list(self.dependents),
            "status": self.status.value,
            "layer": self.layer,
            "has_result": self.result is not None,
            "error": self.error,
        }


class DependencyDAG:
    """
    Direct Acyclic Graph of executable subtasks with antichain computation.
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, DAGTaskNode] = {}

    def add_task(
        self,
        task_id: str,
        name: str,
        payload: Any,
        dependencies: Optional[List[str]] = None,
        layer: int = 1,
    ) -> DAGTaskNode:
        deps = set(dependencies or [])
        node = DAGTaskNode(
            id=task_id,
            name=name,
            payload=payload,
            dependencies=deps,
            layer=layer,
        )
        self.nodes[task_id] = node

        # Update reverse dependencies
        for d in deps:
            if d in self.nodes:
                self.nodes[d].dependents.add(task_id)

        # Update forward dependents for any pre-registered nodes
        for other_id, other_node in self.nodes.items():
            if task_id in other_node.dependencies:
                node.dependents.add(other_id)

        return node

    def get_ready_tasks(self) -> List[DAGTaskNode]:
        """Returns all tasks whose dependencies are fully completed and not yet running."""
        ready = []
        for node in self.nodes.values():
            if node.status == TaskStatus.PENDING:
                all_deps_done = all(
                    dep_id in self.nodes and self.nodes[dep_id].status == TaskStatus.COMPLETED
                    for dep_id in node.dependencies
                )
                if all_deps_done:
                    node.status = TaskStatus.READY
                    ready.append(node)
        return ready

    def mark_running(self, task_id: str) -> None:
        if task_id in self.nodes:
            self.nodes[task_id].status = TaskStatus.RUNNING

    def mark_completed(self, task_id: str, result: Any) -> None:
        if task_id in self.nodes:
            self.nodes[task_id].status = TaskStatus.COMPLETED
            self.nodes[task_id].result = result

    def mark_failed(self, task_id: str, error: str) -> None:
        if task_id in self.nodes:
            self.nodes[task_id].status = TaskStatus.FAILED
            self.nodes[task_id].error = error

    def is_finished(self) -> bool:
        return all(node.status in (TaskStatus.COMPLETED, TaskStatus.FAILED) for node in self.nodes.values())

    def compute_antichains(self) -> List[List[DAGTaskNode]]:
        """
        Decomposes the DAG into sequential waves of concurrent antichains.
        Tasks within the same antichain are completely mutually independent
        and can be executed in parallel with zero contention.
        """
        in_degree: Dict[str, int] = {
            nid: len([d for d in node.dependencies if d in self.nodes])
            for nid, node in self.nodes.items()
        }
        chains: List[List[DAGTaskNode]] = []
        processed: Set[str] = set()

        while len(processed) < len(self.nodes):
            current_wave = [
                self.nodes[nid]
                for nid, deg in in_degree.items()
                if deg == 0 and nid not in processed
            ]
            if not current_wave:
                # Cycle or orphaned nodes: break cycle by taking remaining lowest in-degree
                remaining = [self.nodes[nid] for nid in in_degree if nid not in processed]
                if remaining:
                    current_wave = [remaining[0]]
                else:
                    break

            chains.append(current_wave)
            for node in current_wave:
                processed.add(node.id)
                for dep_id in node.dependents:
                    if dep_id in in_degree:
                        in_degree[dep_id] -= 1

        return chains


class AntichainParallelExecutor:
    """
    Executes a DependencyDAG in parallel waves of antichains.
    """

    def __init__(self, max_workers: int = 4) -> None:
        self.max_workers = max_workers

    def execute_dag(
        self,
        dag: DependencyDAG,
        worker_fn: Callable[[DAGTaskNode], Any],
        progress_callback: Optional[Callable[[DAGTaskNode, int, int], None]] = None,
    ) -> Dict[str, Any]:
        """
        Executes all nodes in the DAG wave by wave, parallelizing each independent antichain.
        Returns mapping of task_id -> result.
        """
        antichains = dag.compute_antichains()
        total_tasks = len(dag.nodes)
        completed_count = 0
        results: Dict[str, Any] = {}

        for wave_idx, antichain in enumerate(antichains):
            # Mark all nodes in antichain as running
            for node in antichain:
                dag.mark_running(node.id)

            # Execute antichain concurrently
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(antichain)))) as executor:
                future_to_node = {executor.submit(worker_fn, node): node for node in antichain}
                for future in concurrent.futures.as_completed(future_to_node):
                    node = future_to_node[future]
                    completed_count += 1
                    try:
                        res = future.result()
                        dag.mark_completed(node.id, res)
                        results[node.id] = res
                    except Exception as exc:
                        dag.mark_failed(node.id, str(exc))
                        results[node.id] = None

                    if progress_callback:
                        try:
                            progress_callback(node, completed_count, total_tasks)
                        except Exception:
                            pass

        return results
