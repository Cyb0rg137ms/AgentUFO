from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agent_core.agentufo_swarm import AgentUFOCoordinator, SeatTokenPolicy
from agent_core.budget_guard import BudgetConfig, HardBudgetGuard
from agent_core.execution_trace import ExecutionTrace
from agent_core.llm.base import BaseLLMProvider, LLMResponse
from agent_core.logos.dag_executor import (
    AntichainParallelExecutor,
    DAGValidationError,
    DependencyDAG,
    TaskStatus,
)
from agent_core.permissions import (
    AuthorizedMCPClient,
    AuthorizedMCPDispatcher,
    MCPToolPolicy,
    PermissionDenied,
    WorkspacePolicy,
)
from agent_core.routing_benchmark import BenchmarkCase, RoutingBenchmark
from agent_core.sandbox import CodeExecutionSandbox
from agent_core.task_evaluation import evaluate_cases, load_cases
from agent_core.workspace_manager import WorkspaceManager


class ParallelExecutionTests(unittest.TestCase):
    def test_independent_tasks_overlap_and_dependent_waits(self):
        dag = DependencyDAG()
        dag.add_task("a", "A", 1)
        dag.add_task("b", "B", 2)
        dag.add_task("c", "C", 3, ["a", "b"])
        lock = threading.Lock()
        active = 0
        peak = 0
        seen = []

        def worker(node):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.12)
            with lock:
                active -= 1
                seen.append(node.id)
            return node.payload * 2

        results = AntichainParallelExecutor(max_workers=2).execute_dag(dag, worker)
        self.assertEqual(results, {"a": 2, "b": 4, "c": 6})
        self.assertGreaterEqual(peak, 2, "independent tasks should overlap in wall-clock time")
        self.assertLess(seen.index("c"), len(seen))
        self.assertEqual(dag.nodes["c"].status, TaskStatus.COMPLETED)

    def test_cycle_rejected_before_any_worker_runs(self):
        dag = DependencyDAG()
        dag.add_task("a", "A", None, ["b"])
        dag.add_task("b", "B", None, ["a"])
        calls = []
        with self.assertRaises(DAGValidationError):
            AntichainParallelExecutor().execute_dag(dag, lambda n: calls.append(n.id))
        self.assertEqual(calls, [])

    def test_unknown_dependency_rejected(self):
        dag = DependencyDAG()
        dag.add_task("a", "A", None, ["missing"])
        with self.assertRaisesRegex(DAGValidationError, "Unknown dependency"):
            dag.validate()

    def test_failed_dependency_blocks_downstream(self):
        dag = DependencyDAG()
        dag.add_task("a", "A", None)
        dag.add_task("b", "B", None, ["a"])
        results = AntichainParallelExecutor().execute_dag(
            dag, lambda n: (_ for _ in ()).throw(RuntimeError("boom")) if n.id == "a" else "bad"
        )
        self.assertIsNone(results["a"])
        self.assertIsNone(results["b"])
        self.assertEqual(dag.nodes["b"].status, TaskStatus.BLOCKED)


class ConcurrentBudgetReservationTests(unittest.TestCase):
    def test_independent_agent_model_calls_overlap_without_double_spending_budget(self):
        class ConcurrentFakeProvider(BaseLLMProvider):
            def __init__(self):
                super().__init__(model_name="fake-concurrent")
                self.lock = threading.Lock()
                self.active = 0
                self.peak = 0

            @property
            def provider_name(self):
                return "fake"

            def generate(
                self, prompt, system_prompt=None, temperature=0.2, max_tokens=4096, **kwargs
            ):
                with self.lock:
                    self.active += 1
                    self.peak = max(self.peak, self.active)
                time.sleep(0.12)
                with self.lock:
                    self.active -= 1
                return LLMResponse(
                    content="ok",
                    model=self.model_name,
                    provider="fake",
                    prompt_tokens=80,
                    completion_tokens=20,
                    total_tokens=100,
                )

            def chat(self, messages, temperature=0.2, max_tokens=4096, **kwargs):
                return self.generate(str(messages), max_tokens=max_tokens)

        with tempfile.TemporaryDirectory() as tmp:
            provider = ConcurrentFakeProvider()
            guard = HardBudgetGuard(
                BudgetConfig(
                    total_token_cap=5000,
                    planner_token_cap=2000,
                    per_seat_token_cap=3000,
                    max_usd_cap=1.0,
                )
            )
            coordinator = AgentUFOCoordinator(
                cloud_llm=provider,
                workspace=WorkspaceManager(workspace_root=Path(tmp)),
                num_seats=3,
                token_policy=SeatTokenPolicy.CLOUD_ONLY,
                budget_guard=guard,
            )
            a, b = coordinator.seats[:2]
            with __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(coordinator._generate_with_budget, seat, provider, f"task {i}")
                    for i, seat in enumerate((a, b))
                ]
                self.assertEqual([f.result().content for f in futures], ["ok", "ok"])
            self.assertEqual(provider.peak, 2, "independent model tasks should genuinely overlap")
            self.assertEqual(guard.total_tokens_used, 200)
            self.assertEqual(guard.seat_tokens_used[a.seat_id], 100)
            self.assertEqual(guard.seat_tokens_used[b.seat_id], 100)
            self.assertEqual(coordinator._reserved_total_tokens, 0)


class TraceAndPermissionTests(unittest.TestCase):
    def test_trace_is_jsonl_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = ExecutionTrace(Path(tmp) / "trace.jsonl", "run-1")
            trace.emit(
                "model.call",
                task_id="t1",
                fields={"api_key": "abc123", "message": "token=sk-abcdefghijklmnop"},
            )
            events = trace.read_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["fields"]["api_key"], "[REDACTED]")
            self.assertNotIn("sk-abcdefghijklmnop", json.dumps(events))

    def test_workspace_policy_blocks_secrets_traversal_and_delete_by_default(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            policy = WorkspacePolicy(Path(tmp))
            self.assertEqual(policy.resolve("src/main.py", "write"), Path(tmp, "src/main.py"))
            with self.assertRaises(PermissionDenied):
                policy.resolve(".env", "read")
            with self.assertRaises(PermissionDenied):
                policy.resolve("../outside.txt", "read")
            with self.assertRaises(PermissionDenied):
                policy.resolve("src/main.py", "delete")
            link = Path(tmp) / "escape"
            try:
                link.symlink_to(Path(outside), target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaises(PermissionDenied):
                policy.resolve("escape/secret", "read")

    def test_workspace_manager_enforces_custom_read_only_policy_on_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            policy = WorkspacePolicy(
                root=root, read_globs=("**", "*"), write_globs=("src/allowed.py",)
            )
            manager = WorkspaceManager(workspace_root=root, policy=policy)
            with self.assertRaises(ValueError):
                manager.write_file_atomic("src/blocked.py", "x", seat_id="builder")
            written = manager.write_file_atomic("src/allowed.py", "ok", seat_id="builder")
            self.assertEqual(written.read_text(encoding="utf-8"), "ok")

    def test_mcp_tool_policy_defaults_to_deny_and_requires_approval(self):
        policy = MCPToolPolicy(allowed_tools={"read_file", "write_file"})
        with self.assertRaises(PermissionDenied):
            policy.authorize("shell")
        with self.assertRaisesRegex(PermissionDenied, "approval"):
            policy.authorize("write_file")
        policy.authorize("write_file", approved=True)
        policy.denied_tools.add("read_file")
        with self.assertRaisesRegex(PermissionDenied, "explicitly denied"):
            policy.authorize("read_file")

    def test_authorized_dispatcher_enforces_policy_before_invoking_handler(self):
        policy = MCPToolPolicy(allowed_tools={"sum"}, approval_required=set())
        dispatcher = AuthorizedMCPDispatcher(policy)
        dispatcher.register("sum", lambda a, b: a + b)
        self.assertEqual(dispatcher.call("sum", {"a": 2, "b": 3}), 5)
        with self.assertRaises(PermissionDenied):
            dispatcher.call("shell", {"command": "echo unsafe"})

    def test_outbound_mcp_client_adapter_authorizes_before_network_call(self):
        calls = []
        policy = MCPToolPolicy(allowed_tools={"filesystem:write_file"})
        client = AuthorizedMCPClient(
            lambda server, tool, args: calls.append((server, tool, args)) or {"ok": True}, policy
        )
        with self.assertRaises(PermissionDenied):
            client.call("filesystem", "delete_file", {"path": "x"})
        self.assertEqual(calls, [], "unauthorized calls must never reach the MCP transport")
        with self.assertRaises(PermissionDenied):
            client.call("filesystem", "write_file", {"path": "x"})
        self.assertEqual(calls, [])
        self.assertEqual(
            client.call("filesystem", "write_file", {"path": "x"}, approved=True), {"ok": True}
        )
        self.assertEqual(len(calls), 1)

    def test_secure_sandbox_fails_closed_without_container_runtime(self):
        sandbox = CodeExecutionSandbox(timeout_seconds=0.2)
        # The test environment may have a container runtime; if not, ensure no host fallback.
        import unittest.mock as mock

        with mock.patch("agent_core.sandbox.shutil.which", return_value=None):
            ok, output = sandbox.execute_untrusted_container("print(1)")
        self.assertFalse(ok)
        self.assertIn("unsafe host fallback is disabled", output)

    def test_default_sandbox_mode_fails_closed_and_project_execution_is_denied(self):
        import unittest.mock as mock

        sandbox = CodeExecutionSandbox(timeout_seconds=0.2)
        self.assertEqual(sandbox.mode, "container")
        with mock.patch("agent_core.sandbox.shutil.which", return_value=None):
            ok, output = sandbox.execute_script(
                "print('must not execute on host')", filename="main.py"
            )
        self.assertFalse(ok)
        self.assertIn("unsafe host fallback is disabled", output)
        ok, output = sandbox.execute_project({"main.py": "print(1)"}, "main.py")
        self.assertFalse(ok)
        self.assertIn("unsafe host fallback is disabled", output)

    def test_host_development_project_mode_still_rejects_path_traversal(self):
        sandbox = CodeExecutionSandbox(timeout_seconds=0.2, mode="host")
        ok, output = sandbox.execute_project(
            {"../escaped.py": "print('bad')", "main.py": "print(1)"}, "main.py"
        )
        self.assertFalse(ok)
        self.assertIn("Unsafe project path", output)

    def test_secure_multifile_project_rejects_traversal_and_missing_entrypoint(self):
        sandbox = CodeExecutionSandbox(timeout_seconds=0.2)
        ok, output = sandbox.execute_project(
            {"../escape.py": "print('bad')", "main.py": "print(1)"}, "main.py"
        )
        self.assertFalse(ok)
        self.assertIn("Unsafe project path", output)
        ok, output = sandbox.execute_project({"main.py": "print(1)"}, "missing.py")
        self.assertFalse(ok)
        self.assertIn("entrypoint", output.lower())

    def test_secure_multifile_project_invokes_container_with_readonly_isolation(self):
        import io
        from unittest import mock

        sandbox = CodeExecutionSandbox(timeout_seconds=1)

        class FakeProcess:
            def __init__(self, command, **kwargs):
                self.command = command
                self.stdout = io.BytesIO(b"project-ok\n")
                self.stderr = io.BytesIO(b"")
                self.returncode = 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        seen = {}

        def popen(command, **kwargs):
            seen["command"] = command
            # Validate the source bundle is staged while the runtime is invoked.
            mount = next(arg for arg in command if arg.startswith("type=bind,src="))
            src = mount.split("src=", 1)[1].split(",dst=", 1)[0]
            self.assertTrue(Path(src, "main.py").is_file())
            self.assertTrue(Path(src, "pkg", "helper.py").is_file())
            return FakeProcess(command, **kwargs)

        with (
            mock.patch("agent_core.sandbox.shutil.which", return_value="/usr/bin/docker"),
            mock.patch("agent_core.sandbox.subprocess.Popen", side_effect=popen),
            mock.patch("agent_core.sandbox.subprocess.run"),
        ):
            ok, output = sandbox.execute_project(
                {
                    "main.py": "from pkg.helper import value\nprint(value())",
                    "pkg/helper.py": "def value(): return 7",
                },
                "main.py",
            )
        self.assertTrue(ok, output)
        self.assertIn("project-ok", output)
        command = seen["command"]
        self.assertIn("--network=none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("--security-opt=no-new-privileges:true", command)
        self.assertIn("readonly", next(arg for arg in command if arg.startswith("type=bind,")))

    def test_host_shell_tool_is_default_deny(self):
        from agent_core.tools import ShellExecuteTool

        result = ShellExecuteTool().execute("echo should-not-run")
        self.assertFalse(result.success)
        self.assertIn("disabled", result.error)


class ReproducibleEvaluationTests(unittest.TestCase):
    def test_routing_benchmark_writes_dataset_hash_and_route_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [BenchmarkCase("c1", "2+2", "4"), BenchmarkCase("c2", "3+3", "6")]
            routes = {
                "baseline": lambda prompt: {
                    "output": str(int(prompt[0]) * 2),
                    "input_tokens": 4,
                    "output_tokens": 2,
                    "cost_usd": 0.01,
                },
                "cheap": lambda prompt: {
                    "output": str(int(prompt[0]) * 2),
                    "input_tokens": 2,
                    "output_tokens": 1,
                    "cost_usd": 0.001,
                },
            }
            report = RoutingBenchmark().run(cases, routes, Path(tmp) / "report.json")
            self.assertEqual(report["summary"]["cheap"]["pass_rate"], 1.0)
            self.assertEqual(report["comparisons"]["cheap"]["cost_savings_fraction"], 0.9)
            self.assertEqual(len(report["dataset_sha256"]), 64)
            self.assertTrue((Path(tmp) / "report.json").exists())

    def test_unlabelled_benchmark_cases_do_not_silently_count_as_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = BenchmarkCase("unlabelled", "write code", None)
            report = RoutingBenchmark().run(
                [case], {"route": lambda _: {"output": "looks good"}}, Path(tmp) / "r.json"
            )
            self.assertEqual(report["summary"]["route"]["pass_rate"], 0.0)

    def test_cost_savings_are_unknown_when_a_route_has_failed_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = BenchmarkCase("one", "2+2", "4")
            routes = {
                "baseline": lambda _: {"output": "4", "cost_usd": 0.01},
                "candidate": lambda _: (_ for _ in ()).throw(RuntimeError("offline")),
            }
            report = RoutingBenchmark().run([case], routes, Path(tmp) / "r.json")
            self.assertFalse(report["summary"]["candidate"]["cost_complete"])
            self.assertIsNone(report["comparisons"]["candidate"]["cost_savings_fraction"])

    def test_task_evaluation_separates_acceptance_from_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate_cases(
                [{"task_id": "t", "prompt": "do", "acceptance": ["test"]}],
                lambda _: {"accepted": True, "verified": False, "status": "review_pending"},
                Path(tmp) / "eval.json",
            )
            self.assertEqual(report["accepted_rate"], 1.0)
            self.assertEqual(report["verified_rate"], 0.0)
            self.assertEqual(report["accepted_and_verified_rate"], 0.0)

    def test_jsonl_loader_rejects_duplicate_ids_and_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cases.jsonl"
            p.write_text(
                '{"task_id":"a","prompt":"x","acceptance":[]}\n{"task_id":"a","prompt":"y","acceptance":[]}\n'
            )
            with self.assertRaisesRegex(ValueError, "unique"):
                load_cases(p)
            p.write_text('{"task_id":"a"}\n')
            with self.assertRaisesRegex(ValueError, "missing required"):
                load_cases(p)

    def test_end_to_end_dag_trace_and_reproducible_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = ExecutionTrace(root / "runs.jsonl", "e2e-run")
            dag = DependencyDAG()
            dag.add_task("plan", "Plan", {"result": "spec"})
            dag.add_task("build", "Build", {"result": "artifact"}, ["plan"])
            outputs = AntichainParallelExecutor(max_workers=2, trace=trace).execute_dag(
                dag, lambda node: node.payload["result"]
            )
            self.assertEqual(outputs, {"plan": "spec", "build": "artifact"})
            events = trace.read_events()
            self.assertEqual(
                [e["event"] for e in events],
                ["task.started", "task.completed", "task.started", "task.completed"],
            )
            cases = [
                {"task_id": "e2e", "prompt": "build artifact", "acceptance": ["artifact exists"]}
            ]
            report = evaluate_cases(
                cases,
                lambda case: {
                    "accepted": outputs["build"] == "artifact",
                    "verified": (dag.nodes["build"].status == TaskStatus.COMPLETED),
                    "status": "completed",
                    "checks": ["dependency-resolved", "worker-result-present"],
                },
                root / "evaluation.json",
            )
            self.assertEqual(report["accepted_and_verified_rate"], 1.0)
            persisted = json.loads((root / "evaluation.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["dataset_sha256"], report["dataset_sha256"])


if __name__ == "__main__":
    unittest.main()
