"""
review_gate.py
==============
Review Gate with Sandbox Execution, Rejection Authority, and Single-Repair Cap.

Ensures that code is tested in real sandbox subprocesses, NOT just ast.parse.
If tests fail, the reviewer attempts exactly ONE repair cycle.
If the repair still fails, it unconditionally REJECTS.
No extra model calls to 'vote' past a red test! A red test is an absolute blocker.
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple, Union

from agent_core.sandbox import CodeExecutionSandbox


class ReviewGateRejectedError(Exception):
    """Raised when code fails sandbox test verification after the maximum allowed repair attempt."""

    pass


@dataclass
class ReviewGateReport:
    """Detailed audit report produced by the Review Gate."""

    task: str
    status: str  # "PASSED", "PASSED_AFTER_REPAIR", "REJECTED"
    passed: bool
    repaired: bool = False
    repair_attempts: int = 0
    max_repair_allowed: int = 1
    execution_time_seconds: float = 0.0
    initial_test_output: str = ""
    final_test_output: str = ""
    exit_code: int = 0
    diff_patch: str = ""
    code_tested: str = ""
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "status": self.status,
            "passed": self.passed,
            "repaired": self.repaired,
            "repair_attempts": self.repair_attempts,
            "max_repair_allowed": self.max_repair_allowed,
            "execution_time_seconds": round(self.execution_time_seconds, 3),
            "exit_code": self.exit_code,
            "passed_tests": self.passed,
            "diff_patch": self.diff_patch,
            "rejection_reason": self.rejection_reason,
            "final_test_output": self.final_test_output[-1000:],  # Tail for UI
        }


class ReviewGate:
    """
    Review Gatekeeper that executes code against real test fixtures in an isolated sandbox.
    Has the authority to reject and veto code that fails tests.
    """

    def __init__(
        self,
        sandbox: Optional[CodeExecutionSandbox] = None,
        timeout_seconds: float = 5.0,
        timeout: Optional[float] = None,
    ) -> None:
        actual_timeout = timeout if timeout is not None else timeout_seconds
        self.sandbox = sandbox or CodeExecutionSandbox(timeout_seconds=actual_timeout)
        self.timeout_seconds = actual_timeout

    def evaluate_and_enforce(
        self,
        task: str,
        code: str,
        test_fixture: Optional[str] = None,
        repair_fn: Optional[Callable[[str, str], str]] = None,
        filename: str = "solution.py",
    ) -> ReviewGateReport:
        """
        Executes code + test fixture in sandbox.
        If tests fail and repair_fn is provided, allows EXACTLY ONE repair attempt.
        If repair fails: unconditionally REJECTS.
        """
        t0 = time.perf_counter()

        # Build execution bundle (code + test fixture)
        test_harness = self._build_test_harness(code, test_fixture)
        success, initial_output = self._run_sandbox(test_harness, filename=filename)

        if success:
            return ReviewGateReport(
                task=task,
                status="PASSED",
                passed=True,
                repaired=False,
                repair_attempts=0,
                execution_time_seconds=time.perf_counter() - t0,
                initial_test_output=initial_output,
                final_test_output=initial_output,
                exit_code=0,
                code_tested=code,
            )

        # Initial run FAILED. Check if repair_fn is provided
        if not repair_fn:
            return ReviewGateReport(
                task=task,
                status="REJECTED",
                passed=False,
                repaired=False,
                repair_attempts=0,
                execution_time_seconds=time.perf_counter() - t0,
                initial_test_output=initial_output,
                final_test_output=initial_output,
                exit_code=1,
                code_tested=code,
                rejection_reason=f"Sandbox test failure (exit code 1):\n{initial_output[:300]}",
            )

        # EXACTLY ONE repair attempt
        repair_attempts = 1
        try:
            repaired_code = repair_fn(code, initial_output)
        except Exception as e:
            return ReviewGateReport(
                task=task,
                status="REJECTED",
                passed=False,
                repaired=False,
                repair_attempts=repair_attempts,
                execution_time_seconds=time.perf_counter() - t0,
                initial_test_output=initial_output,
                final_test_output=f"Repair function crashed: {e}",
                exit_code=1,
                code_tested=code,
                rejection_reason=f"Builder repair crashed: {e}",
            )

        # Calculate diff patch between original and repaired code
        diff = difflib.unified_diff(
            code.splitlines(),
            repaired_code.splitlines(),
            fromfile="original.py",
            tofile="repaired.py",
            lineterm="",
        )
        diff_str = "\n".join(diff)

        # Re-run sandbox on repaired code
        repaired_harness = self._build_test_harness(repaired_code, test_fixture)
        repair_success, repair_output = self._run_sandbox(repaired_harness, filename=filename)

        if repair_success:
            return ReviewGateReport(
                task=task,
                status="PASSED_AFTER_REPAIR",
                passed=True,
                repaired=True,
                repair_attempts=repair_attempts,
                execution_time_seconds=time.perf_counter() - t0,
                initial_test_output=initial_output,
                final_test_output=repair_output,
                exit_code=0,
                diff_patch=diff_str,
                code_tested=repaired_code,
            )

        # Hard stop! Repair failed. Zero model voting past a red test!
        # Hard stop! Repair failed. Zero model voting past a red test!
        return ReviewGateReport(
            task=task,
            status="REJECTED",
            passed=False,
            repaired=True,
            repair_attempts=repair_attempts,
            execution_time_seconds=time.perf_counter() - t0,
            initial_test_output=initial_output,
            final_test_output=repair_output,
            exit_code=1,
            diff_patch=diff_str,
            code_tested=repaired_code,
            rejection_reason=f"Review Gate REJECTED: Code failed verification after 1 repair attempt.\nErrors:\n{repair_output[:400]}",
        )

    # -------------------------------------------------------------------------
    # Project-Level Test Discovery & Merge Gate Enforcement
    # -------------------------------------------------------------------------

    def run_project_tests(
        self,
        project_root: Union[str, Path],
        target_file: Optional[str] = None,
        test_pattern: Optional[str] = None,
        candidate_content: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Run project tests against a temporary workspace overlay containing the candidate.

        The live repository is never modified for testing. Sensitive files and heavyweight
        dependency/build directories are excluded from the test copy.
        """
        import importlib.util
        import os
        import shutil
        import subprocess
        import sys
        import tempfile

        root = Path(project_root).resolve()
        test_dir = root / "tests"
        has_tests_dir = test_dir.exists() and test_dir.is_dir()
        has_test_files = any(root.glob("test_*.py")) or any(root.glob("*_test.py"))
        if not has_tests_dir and not has_test_files:
            return True, "No project test suite discovered in workspace."

        candidate_rel = None
        if candidate_content is not None and target_file:
            candidate = (root / target_file).resolve(strict=False)
            if not candidate.is_relative_to(root):
                return False, "Project test overlay rejected: candidate path escapes the workspace."
            candidate_rel = candidate.relative_to(root)

        excluded_dirs = {
            ".git",
            ".agentufo",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            "dist",
            "build",
            "target",
            ".mypy_cache",
            ".ruff_cache",
        }
        excluded_words = ("secret", "credential", "api_key", "apikey", "password", "token")
        try:
            with tempfile.TemporaryDirectory(prefix="agentufo-review-") as tmp:
                overlay = Path(tmp) / "workspace"
                overlay.mkdir(parents=True, exist_ok=True)
                total_bytes = 0
                copied_files = 0
                for current, dirs, files in os.walk(root):
                    dirs[:] = [d for d in dirs if d not in excluded_dirs and not d.startswith(".")]
                    rel_dir = Path(current).relative_to(root)
                    target_dir = overlay / rel_dir
                    target_dir.mkdir(parents=True, exist_ok=True)
                    for name in files:
                        low = name.lower()
                        if (
                            low in {".env", ".env.local", ".env.production"}
                            or any(word in low for word in excluded_words)
                            or low.endswith((".pem", ".p12", ".key", ".secret"))
                        ):
                            continue
                        src = Path(current) / name
                        if src.is_symlink():
                            continue
                        try:
                            size = src.stat().st_size
                        except OSError:
                            continue
                        if size > 20 * 1024 * 1024:
                            continue
                        total_bytes += size
                        copied_files += 1
                        if total_bytes > 100 * 1024 * 1024 or copied_files > 5000:
                            return (
                                False,
                                "Project test overlay exceeded safety limits (100 MiB or 5,000 files); merge rejected rather than testing the live workspace.",
                            )
                        dst = target_dir / name
                        try:
                            shutil.copy2(src, dst)
                        except OSError as exc:
                            return False, f"Could not prepare isolated project test overlay: {exc}"

                if candidate_rel is not None:
                    dest = overlay / candidate_rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(candidate_content or "", encoding="utf-8")

                env = os.environ.copy()
                env["PYTHONPATH"] = str(overlay) + os.pathsep + env.get("PYTHONPATH", "")
                if importlib.util.find_spec("pytest") is not None:
                    cmd = [sys.executable, "-m", "pytest", "-q"]
                    if test_pattern:
                        cmd.extend(["-k", test_pattern])
                    elif (overlay / "tests").is_dir():
                        cmd.append("tests")
                else:
                    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
                try:
                    res = subprocess.run(
                        cmd,
                        cwd=str(overlay),
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=max(10.0, self.timeout_seconds),
                    )
                    out = (res.stdout + "\n" + res.stderr).strip()
                    if res.returncode == 0 and "Ran 0 tests" in out:
                        return (
                            False,
                            "Project test discovery found zero executable tests; refusing to treat that as verification.",
                        )
                    return (
                        res.returncode == 0,
                        out or f"Test command exited {res.returncode} with no output.",
                    )
                except subprocess.TimeoutExpired:
                    return (
                        False,
                        f"Project test execution timed out after {max(10.0, self.timeout_seconds)} seconds.",
                    )
        except Exception as exc:
            return False, f"Failed to create isolated project test overlay: {exc}"

    def evaluate_merge_candidate(
        self,
        task: str,
        code_candidate: str,
        target_file: str,
        project_root: Optional[Union[str, Path]] = None,
        test_fixture: Optional[str] = None,
        repair_fn: Optional[Callable[[str, str], str]] = None,
    ) -> ReviewGateReport:
        """
        Comprehensive Merge Gate evaluating candidate code against:
        1. Standalone sandbox test fixture.
        2. Real workspace project tests (pytest in repository).
        If ANY test fails and repair fails: unconditionally FAILS MERGE.
        """
        # Step 1: Run isolated test harness in sandbox
        harness = self._build_test_harness(code_candidate, test_fixture)
        fixture_ok, fixture_out = self._run_sandbox(harness, filename=target_file)

        # Step 2: Run repository project tests if root provided
        proj_ok, proj_out = True, ""
        if project_root and fixture_ok:
            proj_ok, proj_out = self.run_project_tests(
                project_root, target_file, candidate_content=code_candidate
            )

        first_pass = fixture_ok and proj_ok
        initial_out = f"Fixture Output:\n{fixture_out}\n\nProject Test Output:\n{proj_out}".strip()

        if first_pass:
            return ReviewGateReport(
                task=task,
                status="MERGE_ACCEPTED",
                passed=True,
                repaired=False,
                repair_attempts=0,
                initial_test_output=initial_out,
                final_test_output=initial_out,
                exit_code=0,
                code_tested=code_candidate,
            )

        if not repair_fn:
            return ReviewGateReport(
                task=task,
                status="MERGE_REJECTED",
                passed=False,
                repaired=False,
                repair_attempts=0,
                initial_test_output=initial_out,
                final_test_output=initial_out,
                exit_code=1,
                code_tested=code_candidate,
                rejection_reason=f"Merge Gate REJECTED: Tests failed without repair function.\n{initial_out[:400]}",
            )

        # Attempt exactly ONE repair cycle
        try:
            repaired_code = repair_fn(code_candidate, initial_out)
        except Exception as rep_err:
            return ReviewGateReport(
                task=task,
                status="MERGE_REJECTED",
                passed=False,
                repaired=False,
                repair_attempts=1,
                initial_test_output=initial_out,
                final_test_output=f"Repair crashed: {rep_err}",
                exit_code=1,
                code_tested=code_candidate,
                rejection_reason=f"Merge Gate REJECTED: Repair cycle crashed: {rep_err}",
            )

        # Re-verify repaired candidate
        rep_harness = self._build_test_harness(repaired_code, test_fixture)
        rep_fixture_ok, rep_fixture_out = self._run_sandbox(rep_harness, filename=target_file)

        rep_proj_ok, rep_proj_out = True, ""
        if project_root and rep_fixture_ok:
            rep_proj_ok, rep_proj_out = self.run_project_tests(
                project_root, target_file, candidate_content=repaired_code
            )

        repair_pass = rep_fixture_ok and rep_proj_ok
        final_out = (
            f"Fixture Output:\n{rep_fixture_out}\n\nProject Test Output:\n{rep_proj_out}".strip()
        )

        diff = difflib.unified_diff(
            code_candidate.splitlines(),
            repaired_code.splitlines(),
            fromfile=f"a/{target_file}",
            tofile=f"b/{target_file}",
            lineterm="",
        )
        diff_str = "\n".join(diff)

        if repair_pass:
            return ReviewGateReport(
                task=task,
                status="MERGE_ACCEPTED_AFTER_REPAIR",
                passed=True,
                repaired=True,
                repair_attempts=1,
                initial_test_output=initial_out,
                final_test_output=final_out,
                exit_code=0,
                diff_patch=diff_str,
                code_tested=repaired_code,
            )

        # Hard failure: Merge rejected!
        return ReviewGateReport(
            task=task,
            status="MERGE_REJECTED",
            passed=False,
            repaired=True,
            repair_attempts=1,
            initial_test_output=initial_out,
            final_test_output=final_out,
            exit_code=1,
            diff_patch=diff_str,
            code_tested=repaired_code,
            rejection_reason=f"Merge Gate REJECTED: Tests still failing after 1 repair attempt. Merge aborted.\n{final_out[:500]}",
        )

    def _run_sandbox(self, code_with_tests: str, filename: str) -> Tuple[bool, str]:
        """Runs code in execution sandbox with network disabled heuristics and resource timeouts."""
        return self.sandbox.execute_script(code_with_tests, filename=filename)

    def _build_test_harness(self, code: str, test_fixture: Optional[str]) -> str:
        """Combines target code with test assertions."""
        if not test_fixture or not test_fixture.strip():
            # If no fixture passed, run the code itself to verify execution & syntax
            return code
        return f"{code}\n\n# --- REVIEW GATE TEST FIXTURE ---\n{test_fixture}\n"
