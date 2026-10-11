from __future__ import annotations

import json
import importlib.util
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_core.llm.base import UnavailableLLMProvider
from agent_core.operator_interface import SandboxOperatorInterface
from agent_core.logos.pondering_engine import PonderingEngine
from agent_core.logos.qdit_accelerator import ExactClassicalVerifier, QditSearchAccelerator, QditTensorEngine
from agent_core.logos.recursive_auditor import RigorousAuditor
from agent_core.logos.holographic_memory import HolographicMemoryEngine
from agent_core.workspace_manager import WorkspaceManager
from server.handlers import ChatSessionManager
from server.web_server import AgentIDERequestHandler


class BudgetGuardTests(unittest.TestCase):
    def test_zero_dollar_cap_allows_local_but_blocks_cloud_before_call(self) -> None:
        from agent_core.budget_guard import BudgetConfig, BudgetCapExceededError, HardBudgetGuard
        guard = HardBudgetGuard(BudgetConfig(max_usd_cap=0.0))
        guard.check_headroom("local-seat", estimated_tokens=100, is_local_tier=True)
        with self.assertRaises(BudgetCapExceededError):
            guard.check_headroom("cloud-seat", estimated_tokens=100, is_local_tier=False)


class ProviderFallbackTests(unittest.TestCase):
    def test_unconfigured_provider_fails_closed(self) -> None:
        provider = UnavailableLLMProvider()
        self.assertFalse(provider.is_available())
        self.assertEqual(provider.provider_name, "unconfigured")
        with self.assertRaisesRegex(RuntimeError, "configure a provider API key"):
            provider.generate("hello")

    def test_review_gate_project_tests_use_candidate_overlay_not_live_file(self) -> None:
        from agent_core.review_gate import ReviewGate
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "module.py").write_text("def value(): return 1\n", encoding="utf-8")
            (root / "tests" / "test_module.py").write_text("from module import value\ndef test_value(): assert value() == 2\n", encoding="utf-8")
            gate = ReviewGate(timeout_seconds=1.0)
            passed, output = gate.run_project_tests(root, "module.py", candidate_content="def value(): return 2\n")
            self.assertTrue(passed, output)
            failed, output = gate.run_project_tests(root, "module.py", candidate_content="def value(): return 1\n")
            self.assertFalse(failed, output)
            self.assertEqual((root / "module.py").read_text(encoding="utf-8"), "def value(): return 1\n")

    def test_syntax_check_does_not_fabricate_confidence(self) -> None:
        operator = SandboxOperatorInterface(llm=UnavailableLLMProvider())
        code, score, details = operator.evaluate_and_elevate_confidence_with_nas(
            "small function", "def add(a, b):\n    return a + b", 0.42
        )
        self.assertIn("def add", code)
        self.assertEqual(score, 0.42)
        self.assertFalse(details["semantic_correctness_verified"])
        self.assertFalse(details["confidence_calibrated"])


class WorkspaceBoundaryTests(unittest.TestCase):
    def test_workspace_lock_normalizes_internal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manager = WorkspaceManager(workspace_root=root)
            self.assertEqual(manager._normalize_rel_path("src/main.py"), "src/main.py")
            self.assertEqual(manager._normalize_rel_path(root / "src" / "main.py"), "src/main.py")

    def test_workspace_lock_rejects_traversal_and_outside_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            manager = WorkspaceManager(workspace_root=Path(tmp))
            with self.assertRaises(ValueError):
                manager._normalize_rel_path("../secrets.txt")
            with self.assertRaises(ValueError):
                manager._normalize_rel_path(Path(outside) / "secrets.txt")

    def test_workspace_lock_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp).resolve()
            manager = WorkspaceManager(workspace_root=root)
            link = root / "escape"
            try:
                link.symlink_to(Path(outside), target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is not available in this environment")
            with self.assertRaises(ValueError):
                manager._normalize_rel_path("escape/secret.txt")

    def test_lock_acquisition_is_thread_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = WorkspaceManager(workspace_root=Path(tmp))
            barrier = threading.Barrier(8)
            outcomes = []
            outcome_lock = threading.Lock()
            def claim(i: int) -> None:
                barrier.wait()
                try:
                    manager.acquire_lock("src/main.py", seat_id=f"seat-{i}")
                    result = "acquired"
                except Exception:
                    result = "conflict"
                with outcome_lock:
                    outcomes.append(result)
            workers = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
            for worker in workers: worker.start()
            for worker in workers: worker.join(timeout=3)
            self.assertEqual(outcomes.count("acquired"), 1)
            self.assertEqual(outcomes.count("conflict"), 7)

    def test_project_read_write_reject_outside_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            manager = ChatSessionManager.__new__(ChatSessionManager)
            manager.project_root = Path(tmp).resolve()
            inside = manager.write_project_file("nested/hello.txt", "hello")
            self.assertEqual(inside["status"], "success")
            self.assertEqual(manager.read_project_file("nested/hello.txt")["content"], "hello")
            escaped = manager.read_project_file(str(Path(outside) / "secret.txt"))
            self.assertEqual(escaped["status"], "error")
            escaped_write = manager.write_project_file(str(Path(outside) / "secret.txt"), "bad")
            self.assertEqual(escaped_write["status"], "error")
            self.assertFalse((Path(outside) / "secret.txt").exists())

    def test_project_tree_rejects_outside_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            manager = ChatSessionManager.__new__(ChatSessionManager)
            manager.project_root = Path(tmp).resolve()
            result = manager.get_project_tree(outside)
            self.assertEqual(result["status"], "error")

    def test_editor_write_uses_atomic_workspace_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manager = ChatSessionManager.__new__(ChatSessionManager)
            manager.project_root = root
            manager.workspace_mgr = WorkspaceManager(workspace_root=root)
            first = manager.write_project_file("main.py", "print('one')\n")
            self.assertEqual(first["status"], "success")
            second = manager.write_project_file("main.py", "print('two')\n")
            self.assertEqual(second["status"], "success")
            self.assertEqual((root / "main.py").read_text(encoding="utf-8"), "print('two')\n")
            backups = list((root / ".agentufo" / "backups").rglob("*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "print('one')\n")


class ReasoningIntegrityTests(unittest.TestCase):
    def test_artifact_preview_does_not_grant_same_origin(self) -> None:
        app = (Path(__file__).parents[1] / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('allow-same-origin', app)
        self.assertNotIn('<iframe', app)

    def test_pondering_uses_95_percent_heuristic_threshold_without_fake_pruning(self) -> None:
        engine = PonderingEngine()
        synthesis = engine.evaluate_multi_angle("write a safe division function")
        self.assertEqual(engine.CONFIDENCE_THRESHOLD, 0.95)
        self.assertEqual(synthesis.elimination_ratio_per_iteration, 0.0)
        self.assertEqual(synthesis.remaining_search_space_fraction, 1.0)
        self.assertIn("not calibrated", synthesis.synthesis_summary.lower())

    def test_auditor_does_not_claim_formal_proof(self) -> None:
        report = RigorousAuditor().audit("def add(a, b):\n    return a + b", "write add function")
        data = report.to_dict()
        self.assertEqual(data["verification_scope"], "heuristic_static_checks_only")
        self.assertFalse(data["runtime_tests_executed"])
        self.assertLessEqual(report.score, 8.0)

    def test_fidelity_normalizes_vectors_and_rejects_shape_mismatch(self) -> None:
        fidelity = ExactClassicalVerifier.verify_reconstruction_fidelity([2, 0], [4, 0])
        self.assertAlmostEqual(fidelity, 1.0)
        self.assertAlmostEqual(ExactClassicalVerifier.verify_reconstruction_fidelity([1, 0], [0, 1]), 0.0)
        with self.assertRaises(ValueError):
            ExactClassicalVerifier.verify_reconstruction_fidelity([1, 2], [1])

    def test_sat_verifier_rejects_missing_assignment_variables(self) -> None:
        self.assertFalse(ExactClassicalVerifier.verify_sat_candidate([[1]], {}))
        self.assertTrue(ExactClassicalVerifier.verify_sat_candidate([[1, -2]], {1: True, 2: False}))
        self.assertFalse(ExactClassicalVerifier.verify_sat_candidate([[]], {}))

    def test_qdit_validates_dimensions_and_does_not_claim_unmeasured_exactness(self) -> None:
        with self.assertRaises(ValueError):
            QditTensorEngine(n_qdits=0)
        calls = []
        candidates = [{"score": 1}, {"score": 3}, {"score": 2}]
        ranked, state = QditSearchAccelerator().compress_and_rank_candidates(
            candidates, lambda item: calls.append(item["score"]) or item["score"], top_k=2
        )
        self.assertEqual([item["score"] for item in ranked], [3, 2])
        self.assertEqual(len(calls), len(candidates))
        self.assertFalse(state.exactness_status)
        self.assertEqual(state.verifier_status, "CLASSICAL_RANKING_UNVERIFIED")
        self.assertFalse(state.certificate["mps_state_used_for_ranking"])

    @unittest.skipIf(importlib.util.find_spec("numpy") is None, "optional Qdit tensor engine requires NumPy")
    def test_qdit_refuses_unvalidated_residual_reconstruction(self) -> None:
        engine = QditTensorEngine(n_qdits=2, d_physical=2, initial_bond_dim=1, max_bond_dim=1)
        engine.neural_cache.discarded_bonds[0] = {
            "U": __import__("numpy").zeros((2, 1)),
            "S": __import__("numpy").ones(1),
            "Vh": __import__("numpy").zeros((1, 2)),
        }
        with self.assertRaises(NotImplementedError):
            engine.reconstruct_state(use_residual=True)

    def test_holographic_memory_enforces_capacity_and_bounds_context(self) -> None:
        memory = HolographicMemoryEngine(dimension=16, max_token_capacity=12)
        memory.store("a" * 16, memory_id="first")  # approximately four tokens
        memory.store("b" * 16, memory_id="second")
        memory.store("c" * 16, memory_id="third")
        memory.store("d" * 16, memory_id="fourth")
        self.assertLessEqual(memory.get_boundary_compression_stats()["total_tokens_stored"], 12)
        self.assertNotIn("first", [record.id for record in memory._records])
        context_memory = HolographicMemoryEngine(dimension=16, max_token_capacity=2000)
        context_memory.store_layer_chunk(1, "long", prompt="p" * 500, response="r" * 1000)
        context = context_memory.build_coherent_context_for_next_layer(current_layer_index=2, max_context_chars=512)
        self.assertLessEqual(len(context), 512)


class HttpContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), AgentIDERequestHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def get_json(self, path: str) -> tuple[int, dict]:
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_endpoint_exposes_setup_state(self) -> None:
        status, payload = self.get_json("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload["service"], "agentufo")
        self.assertIn("setup_required", payload)

    def test_legacy_route_aliases_resolve(self) -> None:
        status, payload = self.get_json("/api/files/tree?folder=.")
        self.assertEqual(status, 200)
        self.assertIn("tree", payload)
        status, payload = self.get_json("/api/artifacts/list")
        self.assertEqual(status, 200)

    def test_missing_static_asset_returns_404(self) -> None:
        req = urllib.request.Request(self.base + "/js/does-not-exist.js")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 404)

    def test_malformed_json_returns_400(self) -> None:
        req = urllib.request.Request(
            self.base + "/api/chat",
            data=b"{invalid-json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 400)

    def test_large_request_body_returns_413(self) -> None:
        # Send only headers: the server must reject from Content-Length before reading body bytes.
        import socket
        sock = socket.create_connection(("127.0.0.1", self.httpd.server_address[1]), timeout=5)
        try:
            sock.sendall((
                "POST /api/chat HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.httpd.server_address[1]}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {10 * 1024 * 1024 + 1}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii"))
            response = sock.recv(4096).decode("latin-1")
            self.assertIn("413", response.split("\r\n", 1)[0])
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()
