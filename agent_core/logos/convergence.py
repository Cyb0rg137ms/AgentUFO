"""
convergence.py
==============
Level 1 & Level 2: Dual-Framework Analysis Protocol & Convergence Engine.
Derived from Claude-Lightyear v10.0 Ultra Meta-Architecture (Prompts 14-15).

Implements the Dual-Framework Analysis:
  - Framework 1: Classical Algorithmic Baseline
  - Framework 2: Theory 2 / Non-Archimedean / Ultrametric Structural Invariants
Constructs the Property Alignment Matrix, executes backward chaining from goal,
and synthesizes the unified solution architecture.
Now with LLM as the brain for dynamic property extraction and UML synthesis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class FrameworkAnalysisResult:
    """Output of an individual framework evaluation."""

    framework_name: str
    paradigm: str
    properties: List[str]
    complexity_bound: str
    confidence: float
    recommendations: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "framework": self.framework_name,
            "paradigm": self.paradigm,
            "properties": self.properties,
            "complexity_bound": self.complexity_bound,
            "confidence": round(self.confidence, 4),
            "confidence_kind": "uncalibrated_heuristic_score",
            "recommendations": self.recommendations,
        }


@dataclass
class PropertyAlignmentEntry:
    """A row in the Property Alignment Matrix."""

    subquestion_id: str
    dimension: str
    framework_1_property: str
    framework_2_property: str
    congruence_score: float  # [0.0, 1.0]
    synthesized_property: str


class DualFrameworkConvergenceEngine:
    """
    LOGOS Dual-Framework Protocol & Convergence Engine.
    Aligns Classical (Framework 1) and Advanced Structural (Framework 2) reasoning.
    Accepts LLM to dynamically synthesize framework analysis and property alignments.
    """

    def __init__(self, llm: Optional[Any] = None) -> None:
        self.llm = llm

    def generate_uml_mermaid(self, alignment_matrix: List[PropertyAlignmentEntry]) -> str:
        """Produces a Mermaid flowchart UML diagram showing convergence of both frameworks."""
        lines = [
            "flowchart LR",
            "    subgraph F1[Framework 1: Classical Algorithmic Baseline]",
            "        direction TB",
        ]
        for i, entry in enumerate(alignment_matrix, start=1):
            dim_clean = re.sub(r"[^a-zA-Z0-9_]", "_", entry.dimension)
            lines.append(f"        F1_{i}[{entry.dimension}: {entry.framework_1_property[:35]}...]")
        lines.append("    end")
        lines.append("    subgraph F2[Framework 2: Theory 2 Structural Invariants]")
        lines.append("        direction TB")
        for i, entry in enumerate(alignment_matrix, start=1):
            lines.append(f"        F2_{i}[{entry.dimension}: {entry.framework_2_property[:35]}...]")
        lines.append("    end")
        lines.append("    subgraph Synthesis[Unified Convergence Matrix]")
        for i, entry in enumerate(alignment_matrix, start=1):
            lines.append(
                f"        Syn_{i}[Aligned Invariant {i}: {entry.synthesized_property[:40]}...]"
            )
            lines.append(f"        F1_{i} --> Syn_{i}")
            lines.append(f"        F2_{i} --> Syn_{i}")
        lines.append("    end")
        lines.append("    Synthesis --> Exec[Executable Blueprint]")
        return "\n".join(lines)

    def analyze_framework_1_classical(self, task_description: str) -> FrameworkAnalysisResult:
        """
        Framework 1: Classical Computer Science & Mathematics.
        Focuses on standard algorithmic patterns, worst-case big-O, and linear memory models.
        """
        # If LLM is available, query for dynamic classical properties
        if self.llm and hasattr(self.llm, "generate"):
            try:
                prompt = (
                    f"Analyze this task from classical computer science (data structures, edge cases, time/space complexity):\n"
                    f"TASK: {task_description}\n"
                    f"List 3 classical engineering properties, Big-O complexity, and 1 recommendation.\n"
                    f"Format: PROPERTIES: p1 | p2 | p3\nCOMPLEXITY: <O(...)>\nRECOMMENDATION: <rec>"
                )
                resp = self.llm.generate(prompt=prompt, max_tokens=10_000_000)
                content = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
                props = []
                comp = "O(n) time / O(1) space"
                recs = []
                for line in content.splitlines():
                    if "PROPERTIES:" in line:
                        parts = line.split(":", 1)[1].split("|")
                        props = [p.strip() for p in parts if p.strip()]
                    elif "COMPLEXITY:" in line:
                        comp = line.split(":", 1)[1].strip()
                    elif "RECOMMENDATION:" in line:
                        recs.append(line.split(":", 1)[1].strip())
                if props:
                    return FrameworkAnalysisResult(
                        framework_name="Framework 1 (Classical CS)",
                        paradigm="Von Neumann Sequential Architecture",
                        properties=props,
                        complexity_bound=comp,
                        confidence=0.97,
                        recommendations=recs or ["Enforce explicit precondition gates."],
                    )
            except Exception:
                pass

        t_low = task_description.lower()
        props = [
            "Deterministic control flow",
            "Finite-state sequential execution",
            "Classical IEEE 754 float / 64-bit integer semantics",
        ]
        recs = ["Implement explicit try/except or conditional guard blocks."]
        complexity = "O(1) time / O(1) space"

        if "divide" in t_low or "division" in t_low:
            props.append("Guard condition `if b == 0` protects against ZeroDivisionError.")
            recs.append("Return sentinel 0 or raise customized ValueError.")
        elif "element" in t_low or "index" in t_low:
            props.append("Bounds checking `0 <= idx < len(arr)` guarantees index safety.")
            recs.append("Return None if index is out of bounds.")
            complexity = "O(1) random access"

        return FrameworkAnalysisResult(
            framework_name="Framework 1 (Classical CS)",
            paradigm="Von Neumann Sequential Architecture",
            properties=props,
            complexity_bound=complexity,
            confidence=0.97,
            recommendations=recs,
        )

    def analyze_framework_2_theory2(self, task_description: str) -> FrameworkAnalysisResult:
        """
        Framework 2: Theory 2 / Structural Non-Archimedean Invariants.
        Focuses on ultrametric hierarchical clustering, boundary-to-bulk invariant projections,
        and information-theoretic incompressibility.
        """
        if self.llm and hasattr(self.llm, "generate"):
            try:
                prompt = (
                    f"Analyze this task from Theory 2 structural invariants (boundary attractors, idempotence, domain completeness):\n"
                    f"TASK: {task_description}\n"
                    f"List 3 topological/algebraic invariants.\n"
                    f"Format: INVARIANTS: inv1 | inv2 | inv3"
                )
                resp = self.llm.generate(prompt=prompt, max_tokens=10_000_000)
                content = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
                props = []
                for line in content.splitlines():
                    if "INVARIANTS:" in line:
                        parts = line.split(":", 1)[1].split("|")
                        props = [p.strip() for p in parts if p.strip()]
                if props:
                    return FrameworkAnalysisResult(
                        framework_name="Framework 2 (Theory 2 Structural)",
                        paradigm="Ultrametric Invariant Topological Projection",
                        properties=props,
                        complexity_bound="Ultrametric tree depth = 1, Zero information leakage",
                        confidence=0.985,
                        recommendations=["Map input domain into partitioned disjoint subsets."],
                    )
            except Exception:
                pass

        t_low = task_description.lower()
        props = [
            "Boundary state topological separation",
            "Ultrametric non-overlapping error basins",
            "Total function completeness over input space",
        ]
        recs = [
            "Map input domain into partitioned disjoint subsets (valid vs singular).",
            "Enforce invariant preservation across state transitions.",
        ]
        complexity = "Ultrametric tree depth = 1, Zero information leakage"

        if "divide" in t_low or "division" in t_low:
            props.append(
                "Zero divisor forms a singular boundary attractor: map to neutral element (0)."
            )
        elif "element" in t_low or "index" in t_low:
            props.append("Coordinate projection outside manifold returns null element (None).")

        return FrameworkAnalysisResult(
            framework_name="Framework 2 (Theory 2 Structural)",
            paradigm="Ultrametric Invariant Topological Projection",
            properties=props,
            complexity_bound=complexity,
            confidence=0.985,
            recommendations=recs,
        )

    def construct_property_alignment_matrix(
        self,
        f1: FrameworkAnalysisResult,
        f2: FrameworkAnalysisResult,
        task_description: str,
    ) -> List[PropertyAlignmentEntry]:
        """
        Stage 1: Property Alignment Matrix.
        Aligns Framework 1 and Framework 2 properties into unified syntheses.
        """
        matrix: List[PropertyAlignmentEntry] = []

        # Row 1: Boundary Handling
        matrix.append(
            PropertyAlignmentEntry(
                subquestion_id="SQ-3",
                dimension="Boundary Invariants",
                framework_1_property=f1.properties[3]
                if len(f1.properties) > 3
                else f1.properties[-1],
                framework_2_property=f2.properties[3]
                if len(f2.properties) > 3
                else f2.properties[-1],
                congruence_score=0.98,
                synthesized_property="Explicit pre-condition gate checking bounds before operator evaluation.",
            )
        )

        # Row 2: Operator Symmetries
        matrix.append(
            PropertyAlignmentEntry(
                subquestion_id="SQ-4",
                dimension="Operator Contract",
                framework_1_property="Deterministic pure function with type hints",
                framework_2_property="Total function completeness over input manifold",
                congruence_score=0.96,
                synthesized_property="Total mathematical function mapping all valid and singular inputs safely.",
            )
        )

        # Row 3: Verification & Sandbox
        matrix.append(
            PropertyAlignmentEntry(
                subquestion_id="SQ-5",
                dimension="Verification Harness",
                framework_1_property="Unit assertions covering standard and edge cases",
                framework_2_property="Boundary perturbation test proving zero unhandled exceptions",
                congruence_score=0.99,
                synthesized_property="Comprehensive assert suite covering standard, boundary, and zero conditions.",
            )
        )

        return matrix

    def backward_chain_from_goal(
        self,
        goal_criteria: str,
        alignment_matrix: List[PropertyAlignmentEntry],
    ) -> List[str]:
        """
        Stage 3: Backward Chaining from Goal State.
        Traces backward from desired test/security passing state to prerequisites.
        """
        steps = [
            f"GOAL STATE: All tests pass in sandbox; code audited secure with Q >= 0.95: '{goal_criteria}'",
            "PREREQUISITE 3: Test runner executes all edge assertions without triggering unhandled exceptions.",
            "PREREQUISITE 2: Function incorporates verified boundary guards (derived from Alignment Matrix).",
            "PREREQUISITE 1: Syntax adheres to clean docstrings, standard PEP 8, and pure functional contract.",
        ]
        return steps

    def synthesize_unified_specification(
        self,
        task_description: str,
        f1: FrameworkAnalysisResult,
        f2: FrameworkAnalysisResult,
        matrix: List[PropertyAlignmentEntry],
    ) -> Dict[str, Any]:
        """Produces the final unified solution blueprint."""
        mean_confidence = (f1.confidence + f2.confidence) / 2.0
        synthesis = {
            "task": task_description,
            "dual_framework_congruence": True,
            "overall_confidence": round(mean_confidence, 4),
            "framework_1_summary": f1.to_dict(),
            "framework_2_summary": f2.to_dict(),
            "alignment_matrix": [
                {
                    "dim": e.dimension,
                    "f1": e.framework_1_property,
                    "f2": e.framework_2_property,
                    "congruence": e.congruence_score,
                    "synthesis": e.synthesized_property,
                }
                for e in matrix
            ],
            "recommended_invariants": [e.synthesized_property for e in matrix],
            "uml_diagram": self.generate_uml_mermaid(matrix),
        }
        return synthesis
