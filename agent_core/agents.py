"""
agents.py
=========
Defines real LLM-backed Developer, Tester, Reviewer, and LOGOS Cognitive agents.
Agents fail explicitly when no provider is configured; no simulated provider output exists.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from agent_core.llm.base import BaseLLMProvider


class BaseAgent:
    """Base Agent class representing an LLM assistant."""

    def __init__(
        self,
        role: str,
        model_name: str = "unconfigured",
        llm: Optional[BaseLLMProvider] = None,
    ) -> None:
        self.role = role
        self.model = model_name
        self.llm = llm

    def generate_response(self, prompt: str, context: str = "") -> str:
        """Generates a text completion based on role and prompt."""
        if self.llm is None:
            raise RuntimeError(f"Agent '{self.role}' has no configured LLM provider; refusing to fabricate a response.")
        resp = self.llm.generate(prompt, system_prompt=f"Role: {self.role}\nContext: {context}")
        return resp.content


class CoderAgent(BaseAgent):
    """Responsible for writing and fixing code files."""

    def __init__(self, llm: Optional[BaseLLMProvider] = None) -> None:
        super().__init__(role="Senior Software Engineer", llm=llm)

    def generate_code(self, task_description: str, error_logs: str = "") -> str:
        """
        Generates Python code to solve a task.
        If error logs are provided, it generates a corrected script.
        """
        if self.llm:
            if error_logs:
                prompt = (
                    f"Task: {task_description}\n"
                    f"Execution Failure Logs:\n{error_logs}\n"
                    "Fix the bug and provide the complete corrected Python code."
                )
            else:
                prompt = f"Task: {task_description}\nWrite clean, documented Python code to solve this task."
            resp = self.llm.generate(prompt, system_prompt=f"Role: {self.role}")
            code = self.llm.extract_code_block(resp.content, "python")
            return code if code else resp.content.strip()

        raise RuntimeError("CoderAgent has no configured LLM provider; no fallback code is generated.")


class TesterAgent(BaseAgent):
    """Responsible for generating test scripts to validate code."""
    __test__ = False

    def __init__(self, llm: Optional[BaseLLMProvider] = None) -> None:
        super().__init__(role="Senior QA Engineer", llm=llm)

    def generate_tests(self, code_to_test: str, task_description: str) -> str:
        """Generates an executable python script to verify code functionality."""
        if self.llm:
            prompt = (
                f"Task: {task_description}\n"
                f"Code:\n```python\n{code_to_test}\n```\n"
                "Generate an executable test script asserting correctness. Include edge cases. Print 'All tests passed' on success."
            )
            resp = self.llm.generate(prompt, system_prompt=f"Role: {self.role}")
            tests = self.llm.extract_code_block(resp.content, "python")
            test_content = tests if tests else resp.content.strip()
            if code_to_test and ("def " not in test_content and "class " not in test_content):
                return f"{code_to_test}\n\n{test_content}"
            return test_content

        raise RuntimeError("TesterAgent has no configured LLM provider; no placeholder tests are generated.")


class ReviewerAgent(BaseAgent):
    """LLM-backed code review; missing or malformed review fails closed."""

    def __init__(self, llm: Optional[BaseLLMProvider] = None) -> None:
        super().__init__(role="Principal Architect", llm=llm)

    def review_code(self, code: str) -> Dict[str, Any]:
        is_safe = "os.system" not in code and "eval(" not in code and "exec(" not in code
        if self.llm is None:
            raise RuntimeError("ReviewerAgent has no configured LLM provider; a heuristic cannot approve code.")
        prompt = (
            f"Review this code for correctness, safety, edge cases, and maintainability:\n```python\n{code}\n```\n"
            "Return JSON with: approved (bool), security_check (passed/failed), style_score (float), findings (array)."
        )
        resp = self.llm.generate(prompt, system_prompt=f"Role: {self.role}")
        try:
            raw = resp.content
            if "{" in raw and "}" in raw:
                raw = raw[raw.find("{"):raw.rfind("}") + 1]
            data = json.loads(raw)
            return {
                "approved": bool(data.get("approved", False)) and is_safe,
                "security_check": "failed" if not is_safe else str(data.get("security_check", "not verified")),
                "style_score": float(data["style_score"]) if isinstance(data.get("style_score"), (int, float)) else None,
                "findings": data.get("findings", []),
                "review_parse_valid": True,
            }
        except (ValueError, TypeError, KeyError):
            return {"approved": False, "security_check": "not verified", "style_score": None,
                    "findings": ["Reviewer response was not valid structured JSON; fail-closed."],
                    "review_parse_valid": False}



class PonderingAgent(BaseAgent):
    """LOGOS Agent specialized in horizontal thinking across 5 orthogonal angles."""

    def __init__(self, llm: Optional[BaseLLMProvider] = None) -> None:
        super().__init__(role="LOGOS Pondering Strategist", llm=llm)

    def ponder(self, task: str) -> Dict[str, Any]:
        from agent_core.logos.pondering_engine import PonderingEngine
        engine = PonderingEngine()
        synthesis = engine.evaluate_multi_angle(task)
        return {
            "summary": synthesis.synthesis_summary,
            "qualified_invariants": [p.to_dict() for p in synthesis.qualified_properties],
            "rejected_hypotheses": [p.to_dict() for p in synthesis.rejected_properties],
            "remaining_space_fraction": synthesis.remaining_search_space_fraction,
        }


class LogosArchitectAgent(BaseAgent):
    """LOGOS Cognitive Architect orchestrating MERA coarse-graining and Dual-Framework convergence."""

    def __init__(self, llm: Optional[BaseLLMProvider] = None) -> None:
        super().__init__(role="LOGOS Chief Cognitive Architect", llm=llm)

    def plan_architecture(self, task: str) -> Dict[str, Any]:
        from agent_core.logos.decomposition import MasterIndexDecomposer
        from agent_core.logos.convergence import DualFrameworkConvergenceEngine
        from agent_core.logos.nas_topology import NASTopologyEngine

        decomposer = MasterIndexDecomposer()
        convergence = DualFrameworkConvergenceEngine()
        nas = NASTopologyEngine()

        subquestions = decomposer.decompose(task)
        f1 = convergence.analyze_framework_1_classical(task)
        f2 = convergence.analyze_framework_2_theory2(task)
        alignment = convergence.construct_property_alignment_matrix(f1, f2, task)
        topology = nas.search_optimal_topology(task)

        return {
            "topology": topology.to_dict(),
            "subquestions": [sq.to_dict() for sq in subquestions],
            "framework_1": f1.to_dict(),
            "framework_2": f2.to_dict(),
            "alignment_matrix": [
                {
                    "dimension": e.dimension,
                    "synthesized_property": e.synthesized_property,
                    "congruence": e.congruence_score,
                }
                for e in alignment
            ],
        }
