"""
orchestrator.py
===============
Coordinates the agentic coding loop: Coder, Tester, and Reviewer interaction
with sandbox code execution.
Enhanced with Claude-Lightyear v10.0 Ultra / LOGOS cognitive architecture support,
allowing dynamic NAS topology selection, horizontal pondering, and universal LLM execution.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from agent_core.agents import CoderAgent, ReviewerAgent, TesterAgent
from agent_core.llm.base import BaseLLMProvider
from agent_core.sandbox import CodeExecutionSandbox


class AgenticIDEOrchestrator:
    """Manages the iterative code generation, testing, and debugging loop."""

    def __init__(
        self,
        max_retries: int = 3,
        llm: Optional[BaseLLMProvider] = None,
        enable_logos: bool = False,
    ) -> None:
        self.llm = llm
        self.enable_logos = enable_logos
        self.coder = CoderAgent(llm=llm)
        self.tester = TesterAgent(llm=llm)
        self.reviewer = ReviewerAgent(llm=llm)
        self.sandbox = CodeExecutionSandbox()
        self.max_retries = max_retries

        # LOGOS components (lazily initialized if enable_logos is True)
        self._logos_decomposer = None
        self._logos_nas = None
        self._logos_pondering = None
        self._logos_holographic = None

        if self.enable_logos:
            self._init_logos()

    def _init_logos(self) -> None:
        from agent_core.logos.decomposition import MasterIndexDecomposer
        from agent_core.logos.holographic_memory import HolographicMemoryEngine
        from agent_core.logos.nas_topology import NASTopologyEngine
        from agent_core.logos.pondering_engine import PonderingEngine

        self._logos_decomposer = MasterIndexDecomposer()
        self._logos_nas = NASTopologyEngine()
        self._logos_pondering = PonderingEngine()
        self._logos_holographic = HolographicMemoryEngine(dimension=128)

    def run_development_cycle(self, task: str) -> Dict[str, Any]:
        """
        Runs the complete developer loop:
        1. (Optional LOGOS) Decompose task, search NAS topology, ponder horizontal angles.
        2. Coder generates code.
        3. Tester generates validation scripts.
        4. Sandbox executes test script.
        5. If tests fail, Coder receives logs to debug/fix. Loop retries.
        6. If tests pass, Reviewer audits code.

        Args:
            task: Task description.

        Returns:
            Dictionary with final code, execution logs, and compilation status.
        """
        print(f"\n[ORCHESTRATOR] Starting development cycle for task: '{task}'")
        topology_name = "linear_pipeline"
        logos_telemetry = None

        if self.enable_logos and self._logos_nas:
            print("[ORCHESTRATOR] Activating LOGOS cognitive reasoning & NAS topology search...")
            topology = self._logos_nas.search_optimal_topology(task)
            topology_name = topology.topology_type.value
            pondering = self._logos_pondering.evaluate_multi_angle(task)
            subquestions = self._logos_decomposer.decompose(task)

            logos_telemetry = {
                "topology": topology_name,
                "subquestions_count": len(subquestions),
                "qualified_properties_count": pondering.qualified_properties_count,
                "remaining_search_space": pondering.remaining_search_space_fraction,
                "summary": pondering.synthesis_summary,
            }
            print(f"[ORCHESTRATOR] NAS selected topology: '{topology_name}'")
            print(
                f"[ORCHESTRATOR] Pondering: {pondering.qualified_properties_count} heuristic scores met the configured threshold; this is not formal verification."
            )

        # Step 1: Initial draft generation
        code = self.coder.generate_code(task)
        error_logs = ""
        logs = ""

        for attempt in range(self.max_retries):
            print(f"[ORCHESTRATOR] Attempt {attempt + 1:02d} - Validating code structure...")

            # Step 2: Generate test suite
            tests = self.tester.generate_tests(code, task)

            # Step 3: Execute in sandbox
            success, logs = self.sandbox.execute_script(tests, "test_runner.py")

            if success:
                print("[ORCHESTRATOR] Success! All tests passed in sandbox.")
                # Step 4: Code review
                review = self.reviewer.review_code(code)
                result = {
                    "status": "success",
                    "attempts": attempt + 1,
                    "final_code": code,
                    "test_logs": logs,
                    "reviewer_audit": review,
                }
                if logos_telemetry:
                    result["logos_telemetry"] = logos_telemetry
                return result
            else:
                print("[ORCHESTRATOR] Test failed. Capturing execution error logs...")
                error_logs = logs
                # Feed error logs back to coder for repair
                code = self.coder.generate_code(task, error_logs)

        # Loop exhausted without success
        result = {
            "status": "failed",
            "attempts": self.max_retries,
            "final_code": code,
            "error_logs": error_logs,
            "reviewer_audit": None,
        }
        if logos_telemetry:
            result["logos_telemetry"] = logos_telemetry
        return result
