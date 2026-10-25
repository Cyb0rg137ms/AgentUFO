"""
decomposition.py
================
Level 0: Master Index & Subquestion Taxonomy Engine.
Derived from Claude-Lightyear v10.0 Ultra Framework (Level 0 Meta-Architecture).

Breaks down any meta-task into a structured taxonomy of hierarchical subquestions,
computes dependency topological order, and constructs the Master Index blueprint.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Subquestion:
    """A decomposed subquestion representing an angle of inquiry."""

    id: str
    dimension: str
    question: str
    dependencies: List[str] = field(default_factory=list)
    priority: int = 1
    properties_framework1: List[str] = field(default_factory=list)  # Classical
    properties_framework2: List[str] = field(default_factory=list)  # Theory 2
    confidence: float = 0.0
    resolved: bool = False
    findings: str = ""
    layer: int = 1
    parent_id: Optional[str] = None
    sub_components: List[Subquestion] = field(default_factory=list)
    code_snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "dimension": self.dimension,
            "question": self.question,
            "dependencies": self.dependencies,
            "priority": self.priority,
            "properties_f1": self.properties_framework1,
            "properties_f2": self.properties_framework2,
            "confidence": round(self.confidence, 4),
            "resolved": self.resolved,
            "findings": self.findings,
            "layer": self.layer,
            "parent_id": self.parent_id,
            "sub_components": [c.to_dict() for c in self.sub_components],
            "code_snippet": self.code_snippet,
        }


class MasterIndexDecomposer:
    """
    Decomposes programming, mathematical, and algorithmic problems
    into the Lightyear v10.0 Level 0 Subquestion Taxonomy:
      - Dimension 1: Information Theory & Atomicity (Kolmogorov incompressibility, entropy)
      - Dimension 2: Complexity Bounds & Space Reducibility (O-bounds, recursivity)
      - Dimension 3: Boundary Invariants & Edge Singularities (zero-divisions, bounds, overflows)
      - Dimension 4: Operator Symmetries & Type Contracts (algebraic properties, interfaces)
      - Dimension 5: Execution Sandbox & Verification Criteria (unit tests, security audit)
    """

    TAXONOMY_DIMENSIONS = [
        ("DIM_INFO", "Information Structure & State Density"),
        ("DIM_COMPLEXITY", "Computational Complexity & Reducibility"),
        ("DIM_BOUNDARY", "Boundary Conditions & Invariant Singularities"),
        ("DIM_OPERATOR", "Operator Algebra & Interface Contract"),
        ("DIM_VERIFICATION", "Sandbox Execution & Formal Verification"),
    ]

    def __init__(self, llm: Optional[Any] = None) -> None:
        self.llm = llm

    def is_evasive_or_impossible(self, text: str) -> bool:
        """
        Generically detects whether an LLM response declares a task impossible/unfeasible,
        refuses execution, or provides generic non-executable hedging instead of technical substance.
        """
        if not text or len(text.strip()) < 15:
            return True
        t_low = text.lower()
        evasive_patterns = [
            r"\b(?:it(?:'s| is) )?impossible to\b",
            r"\bnot feasible\b",
            r"\bcannot (?:create|build|generate|implement|produce|do|write)\b",
            r"\bcan(?:'t| not) be (?:implemented|built|done|created)\b",
            r"\bunable to (?:create|build|implement|generate|do)\b",
            r"\bas an ai(?: language model)?,? (?:i cannot|i am unable)\b",
            r"\bi cannot provide (?:a full|the complete|working|entire)\b",
            r"\bi can only (?:provide|give|guide) (?:guidance|advice|an overview|tips|steps|general)\b",
            r"\bbeyond (?:the|my) scope\b",
            r"\btoo complex to (?:implement|build|create)\b",
            r"\bnot possible to (?:implement|create|build)\b",
            r"\bi'?m unable to\b",
            r"\bit is not possible\b",
            r"\bnot realistically possible\b",
            r"\bexceeds the capabilities\b",
        ]
        for pattern in evasive_patterns:
            if re.search(pattern, t_low):
                return True

        # Check if response is purely generic advice without code or actionable architectural specifications
        has_spec = any(
            w in t_low
            for w in [
                "def ",
                "class ",
                "interface",
                "function",
                "module",
                "step 1",
                "component",
                "subsystem",
                "algorithm",
                "contract:",
            ]
        )
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if (
            len(lines) < 3
            and not has_spec
            and any(w in t_low for w in ["guidance", "tips", "consider", "suggest"])
        ):
            return True

        return False

    def reframe_evasive_response(self, task: str, evasive_text: str) -> str:
        """
        Transforms generic hedging or 'impossible' claims into concrete, actionable engineering requirements.
        Generically instructs the LLM to specify everything needed to execute the prompt without declaring impossibility.
        """
        if (
            not self.llm
            or not hasattr(self.llm, "generate")
            or not getattr(self.llm, "is_available", lambda: True)()
        ):
            return (
                f"Actionable Implementation Blueprint for '{task}':\n"
                "1. Core State & Data Representation\n"
                "2. Algorithmic Computation Pipeline\n"
                "3. Boundary Condition Guards & Invariant Enforcement\n"
                "4. High-Performance Execution & Verification Harness"
            )

        prompt = (
            f"TARGET GOAL: {task}\n\n"
            f"PREVIOUS RESPONSE (HEDGING / DECLARED IMPOSSIBLE):\n{evasive_text[:1200]}\n\n"
            "TRANSFORMATION INSTRUCTION:\n"
            "The previous response gave generic guidance, disclaimers, or claimed it is impossible / not feasible. "
            "Transform this completely: What are ALL the concrete, actionable technical components, modules, data structures, "
            "algorithms, subsystem interfaces, and implementation steps the LLM needs to build and execute? "
            "DO NOT say impossible or not feasible. DO NOT give generic non-actionable disclaimers or suggestions. "
            "Provide the complete, unconstrained, technical operational blueprint and concrete requirements."
        )
        try:
            resp = self.llm.generate(
                prompt=prompt,
                system_prompt="You are a Principal Systems Architect. Transform evasive/impossible claims into concrete, actionable engineering specifications.",
                max_tokens=10_000_000,
            )
            content = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
            if content and len(content) > 30 and not self.is_evasive_or_impossible(content):
                return content
        except Exception:
            pass

        return (
            f"Actionable Operational Blueprint for '{task}':\n"
            "1. State Representation and Type Structures\n"
            "2. Core Business Logic and Algorithmic Engine\n"
            "3. Boundary Invariants and Singularity Guards\n"
            "4. Component Contract and Test Verification Suite"
        )

    def explore_impressive_execution(self, prompt_text: str) -> str:
        """
        Queries the LLM for the best, most impressive way to execute the prompt.
        If the response indicates it's impossible or gives generic answers,
        reframes it into actionable specifications without impossibility claims.
        """
        exploration_query = (
            f"What is the absolute best, most impressive, frontier, high-performance way to architect and execute this task:\n"
            f"'{prompt_text}'?\n\n"
            "Specify the highest-standard modular architecture, state invariants, zero-latency algorithms, and execution strategy."
        )
        response_text = ""
        if (
            self.llm
            and hasattr(self.llm, "generate")
            and getattr(self.llm, "is_available", lambda: True)()
        ):
            try:
                resp = self.llm.generate(
                    prompt=exploration_query,
                    system_prompt="You are a Frontier Systems Architect designing state-of-the-art solutions. Detail the most impressive executable architecture.",
                    max_tokens=10_000_000,
                )
                response_text = (
                    resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
                )
            except Exception:
                response_text = ""

        if not response_text:
            response_text = f"Frontier execution architecture for '{prompt_text}' with modular subsystems and rigorous boundary guarantees."

        if self.is_evasive_or_impossible(response_text):
            response_text = self.reframe_evasive_response(prompt_text, response_text)

        return response_text

    def decompose_recursive(
        self,
        meta_task: str,
        max_depth: int = 2,
        max_workers: int = 4,
    ) -> Dict[str, Any]:
        """
        Executes a configurable multi-layer parallel recursive fractal decomposition:
        - Root: Explores most impressive execution -> reframes if evasive -> decomposes into Layer 1 subtasks.
        - Deeper Layers: PARALLELLY decomposes each subtask concurrently via ThreadPoolExecutor,
                         treating each subtask as its own frontier task with evasion reframing.
        - Returns DAG representation, subquestions at each layer, leaf tasks, and full tree.
        """
        task_clean = meta_task.strip()
        root_actionable = self.explore_impressive_execution(task_clean)

        # Layer 1 Decomposition
        layer1_subqs = self.decompose(root_actionable)
        for idx, sq in enumerate(layer1_subqs):
            sq.layer = 1
            sq.id = f"L1-SQ-{idx + 1}"

        tree: Dict[str, List[Subquestion]] = {}
        all_layers: Dict[int, List[Subquestion]] = {1: layer1_subqs}

        current_parents = layer1_subqs
        for depth in range(2, max_depth + 1):
            next_level_nodes: List[Subquestion] = []

            def _decompose_single_parent(
                parent_sq: Subquestion,
            ) -> Tuple[Subquestion, List[Subquestion]]:
                sub_prompt = parent_sq.question
                p_actionable = self.explore_impressive_execution(sub_prompt)
                raw_children = self.decompose(p_actionable)
                chosen = raw_children
                c_nodes: List[Subquestion] = []
                for c_idx, c_sq in enumerate(chosen):
                    c_sq.layer = depth  # noqa: B023 - map() completes inside this loop iteration
                    c_sq.parent_id = parent_sq.id
                    c_sq.id = f"{parent_sq.id}-L{depth}-{c_idx + 1}"  # noqa: B023
                    # Inherit parent dependency or link to previous sibling
                    if c_idx > 0:
                        c_sq.dependencies = [f"{parent_sq.id}-L{depth}-{c_idx}"]  # noqa: B023
                    c_nodes.append(c_sq)
                return parent_sq, c_nodes

            # PARALLEL DECOMPOSITION ACROSS ALL PARENT SUBTASKS
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(max_workers, max(1, len(current_parents)))
            ) as executor:
                futures = [executor.submit(_decompose_single_parent, p) for p in current_parents]
                for fut in concurrent.futures.as_completed(futures):
                    p_node, c_nodes = fut.result()
                    p_node.sub_components = c_nodes
                    tree[p_node.id] = c_nodes
                    next_level_nodes.extend(c_nodes)

            all_layers[depth] = next_level_nodes
            current_parents = next_level_nodes

        leaf_subquestions = all_layers[max_depth]

        # Build DependencyDAG
        from agent_core.logos.dag_executor import DependencyDAG

        dag = DependencyDAG()
        for layer_num, sq_list in all_layers.items():
            for sq in sq_list:
                dag.add_task(
                    task_id=sq.id,
                    name=f"[{sq.dimension}] {sq.question[:50]}",
                    payload=sq,
                    dependencies=sq.dependencies,
                    layer=layer_num,
                )

        return {
            "root_task": task_clean,
            "actionable_spec": root_actionable,
            "layer1_subquestions": layer1_subqs,
            "layer2_leaf_subquestions": leaf_subquestions,
            "all_layers": all_layers,
            "tree": tree,
            "dag": dag,
        }

    def decompose_recursive_two_layers(self, meta_task: str) -> Dict[str, Any]:
        """
        Backwards-compatible interface calling parallel recursive decomposition at max_depth=2.
        """
        return self.decompose_recursive(meta_task, max_depth=2)

    def decompose(self, meta_task: str) -> List[Subquestion]:
        """
        Decomposes a meta task into a structured list of subquestions.
        Uses the LLM as the cognitive brain when available, with deterministic fallback.
        """
        task_clean = meta_task.strip()

        # Dynamic LLM cognitive decomposition
        if (
            self.llm
            and hasattr(self.llm, "generate")
            and getattr(self.llm, "is_available", lambda: True)()
        ):
            try:
                system_prompt = (
                    "You are the LOGOS Meta-Architecture Engine. Dynamically break down the user's task into "
                    "all necessary irreducible architectural subquestions and execution layers with dependencies. "
                    "Output ONLY a raw JSON array matching this format:\n"
                    '[\n  {\n    "id": "SQ-1",\n    "dimension": "Information Structure & State Density",\n'
                    '    "question": "Specific question about state...",\n    "dependencies": [],\n'
                    '    "priority": 1,\n    "confidence": 0.96,\n    "findings": "Key invariant strategy..."\n  }\n]'
                )
                resp = self.llm.generate(
                    prompt=f"Task to decompose: {task_clean}",
                    system_prompt=system_prompt,
                    temperature=0.1,
                    max_tokens=10_000_000,
                )
                text = resp.content.strip()
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()

                parsed = json.loads(text)
                if isinstance(parsed, list) and len(parsed) >= 1:
                    subqs = []
                    for item in parsed:
                        subqs.append(
                            Subquestion(
                                id=item.get("id", f"SQ-{len(subqs) + 1}"),
                                dimension=item.get("dimension", "Architectural Invariant"),
                                question=item.get("question", task_clean),
                                dependencies=item.get("dependencies", []),
                                priority=int(item.get("priority", 1)),
                                confidence=max(0.0, min(1.0, float(item.get("confidence", 0.0)))),
                                findings=item.get("findings", ""),
                            )
                        )
                    if subqs:
                        return subqs
            except Exception:
                pass

        # Deterministic Structural Fallback
        subquestions: List[Subquestion] = []

        # 1. Information Structure
        sq1 = Subquestion(
            id="SQ-1",
            dimension="Information Structure & State Density",
            question=f"What are the minimal irreducible state representations and input types for: '{task_clean}'?",
            dependencies=[],
            priority=1,
            confidence=0.65,
        )
        subquestions.append(sq1)

        # 2. Computational Complexity & Reducibility
        sq2 = Subquestion(
            id="SQ-2",
            dimension="Computational Complexity & Reducibility",
            question=f"What are the optimal algorithmic time/space bounds and can search space be pruned recursively for: '{task_clean}'?",
            dependencies=["SQ-1"],
            priority=2,
            confidence=0.60,
        )
        subquestions.append(sq2)

        # 3. Boundary Invariants
        sq3 = Subquestion(
            id="SQ-3",
            dimension="Boundary Conditions & Invariant Singularities",
            question=f"What boundary conditions, null/zero cases, overflows, or edge exceptions must be guarded against in: '{task_clean}'?",
            dependencies=["SQ-1"],
            priority=2,
            confidence=0.65,
        )
        subquestions.append(sq3)

        # 4. Operator Algebra & Interface Contract
        sq4 = Subquestion(
            id="SQ-4",
            dimension="Operator Algebra & Interface Contract",
            question=f"What function signatures, type invariants, and pure idempotence guarantees satisfy: '{task_clean}'?",
            dependencies=["SQ-2", "SQ-3"],
            priority=3,
            confidence=0.60,
        )
        subquestions.append(sq4)

        # 5. Sandbox Execution & Verification
        sq5 = Subquestion(
            id="SQ-5",
            dimension="Sandbox Execution & Formal Verification",
            question=f"What tests provide meaningful evidence of correctness for: '{task_clean}'?",
            dependencies=["SQ-4"],
            priority=4,
            confidence=0.70,
        )
        subquestions.append(sq5)

        return subquestions

    def topological_sort(self, subquestions: List[Subquestion]) -> List[Subquestion]:
        """Sorts subquestions topologically based on dependency graphs."""
        sq_map = {sq.id: sq for sq in subquestions}
        visited = set()
        order = []

        def dfs(sq_id: str):
            if sq_id in visited or sq_id not in sq_map:
                return
            visited.add(sq_id)
            for dep_id in sq_map[sq_id].dependencies:
                dfs(dep_id)
            order.append(sq_map[sq_id])

        for sq in subquestions:
            dfs(sq.id)

        return order

    def generate_uml_mermaid(self, subquestions: List[Subquestion]) -> str:
        """Generates a Mermaid UML sequence / dependency diagram for the decomposed task."""
        lines = ["graph TD"]
        for sq in subquestions:
            clean_dim = re.sub(r"[^a-zA-Z0-9 _-]", "", sq.dimension)
            clean_q = re.sub(r"[^a-zA-Z0-9 _-]", "", sq.question)[:35]
            lines.append(f'    {sq.id}["<b>{sq.id}</b>: {clean_dim}<br/><i>{clean_q}...</i>"]')
            for dep in sq.dependencies:
                lines.append(f"    {dep} --> {sq.id}")
        return "\n".join(lines)


# =======================================================================
# Dynamic Blueprint Engine & Architectural Decompositions
# =======================================================================


@dataclass
class BlueprintComponent:
    """A modular component in the architectural layout todo list."""

    id: str
    name: str
    subsystem: str
    purpose: str
    contract: str
    dependencies: List[str] = field(default_factory=list)
    status: str = "completed"  # pending, in_progress, completed
    invariant_guard: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "subsystem": self.subsystem,
            "purpose": self.purpose,
            "contract": self.contract,
            "dependencies": self.dependencies,
            "status": self.status,
            "invariant_guard": self.invariant_guard,
        }


@dataclass
class ArchitectureBlueprint:
    """Master Architectural Blueprint and Component Todo Breakdown."""

    meta_task: str
    topology: str
    system_layout: str
    subsystems: List[str]
    components: List[BlueprintComponent]
    global_invariants: List[str]
    total_components: int = 0

    def __post_init__(self):
        self.total_components = len(self.components)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "meta_task": self.meta_task,
            "topology": self.topology,
            "system_layout": self.system_layout,
            "subsystems": self.subsystems,
            "components": [c.to_dict() for c in self.components],
            "global_invariants": self.global_invariants,
            "total_components": self.total_components,
        }

    def format_holographic_entry(self) -> str:
        """Serializes the blueprint for holographic memory without arbitrary truncation."""
        comp_summary = "; ".join(f"[{c.id}] {c.name} ({c.contract})" for c in self.components)
        return (
            f"BLUEPRINT::{self.topology.upper()}::{self.system_layout}::"
            f"COMPONENTS({len(self.components)})::{comp_summary}::"
            f"INVARIANTS::{' | '.join(self.global_invariants)}"
        )


class BlueprintEngine:
    """
    Dynamic Blueprint Engine driven directly by the LLM.
    Transforms arbitrary user tasks into coherent, unconstrained component architectures
    with zero hardcoded templates and zero caps on the number of components or subsystems.
    """

    def __init__(self, llm: Optional[Any] = None) -> None:
        self.llm = llm

    def generate_blueprint(
        self,
        task_description: str,
        topology_type: str = "linear_pipeline",
        invariants: Optional[List[str]] = None,
    ) -> ArchitectureBlueprint:
        task_clean = task_description.strip()
        active_invariants = invariants or [
            "Input validation gates must assert valid types and domain bounds",
            "State representation must minimize Kolmogorov redundancy and avoid duplicate state copies",
            "Function must be referentially transparent and idempotent across repeated calls",
            "Security boundary: zero unconstrained subshell escapes or malicious bytecodes",
        ]

        # 1. Dynamic LLM synthesis
        if (
            self.llm
            and hasattr(self.llm, "generate")
            and getattr(self.llm, "is_available", lambda: True)()
        ):
            try:
                system_prompt = (
                    "You are the Principal Systems Architect. Decompose the user's task into an unconstrained, "
                    "production-ready modular architectural blueprint and component todo list. "
                    "Determine dynamically as many subsystems and components as strictly needed for zero-defect implementation. "
                    "Output ONLY a raw JSON object matching this schema:\n"
                    "{\n"
                    '  "system_layout": "<Comprehensive System Architecture Strategy>",\n'
                    '  "subsystems": ["<Subsystem 1>", "<Subsystem 2>", ...],\n'
                    '  "components": [\n'
                    "    {\n"
                    '      "id": "comp_01",\n'
                    '      "name": "<Component Name>",\n'
                    '      "subsystem": "<Subsystem Name>",\n'
                    '      "purpose": "<Concrete responsibility>",\n'
                    '      "contract": "<Functions/APIs/Interfaces>",\n'
                    '      "dependencies": [],\n'
                    '      "invariant_guard": "<Safety and boundary invariant>"\n'
                    "    }\n"
                    "  ],\n"
                    '  "global_invariants": ["<Invariant 1>", ...]\n'
                    "}"
                )
                resp = self.llm.generate(
                    prompt=f"TARGET TASK:\n{task_clean}\n\nTOPOLOGY: {topology_type}\nKNOWN INVARIANTS:\n"
                    + "\n".join(f"- {i}" for i in active_invariants),
                    system_prompt=system_prompt,
                    temperature=0.1,
                    max_tokens=10_000_000,
                )
                text = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()

                parsed = json.loads(text)
                if (
                    isinstance(parsed, dict)
                    and "components" in parsed
                    and len(parsed["components"]) > 0
                ):
                    comps = []
                    for idx, c in enumerate(parsed["components"]):
                        comps.append(
                            BlueprintComponent(
                                id=c.get("id", f"comp_{idx + 1:02d}"),
                                name=c.get("name", f"Component {idx + 1}"),
                                subsystem=c.get("subsystem", "Core Subsystem"),
                                purpose=c.get("purpose", ""),
                                contract=c.get("contract", ""),
                                dependencies=c.get("dependencies", []),
                                invariant_guard=c.get("invariant_guard", ""),
                            )
                        )
                    subsystems = parsed.get("subsystems") or list({c.subsystem for c in comps})
                    layout = (
                        parsed.get("system_layout")
                        or f"Dynamic Modular Architecture for {task_clean[:60]}"
                    )
                    invs = parsed.get("global_invariants") or active_invariants
                    return ArchitectureBlueprint(
                        meta_task=task_clean,
                        topology=topology_type,
                        system_layout=layout,
                        subsystems=subsystems,
                        components=comps,
                        global_invariants=invs,
                    )
            except Exception:
                pass

        # 2. Dynamic Fallback Generation
        t_low = task_clean.lower()
        is_interactive = any(
            w in t_low
            for w in [
                "game",
                "canvas",
                "temple run",
                "flappy",
                "tetris",
                "pong",
                "play",
                "interactive",
                "animation",
            ]
        )
        is_math = any(
            w in t_low
            for w in [
                "divide",
                "matrix",
                "vector",
                "sort",
                "algorithm",
                "fibonacci",
                "prime",
                "math",
                "quicksort",
            ]
        )

        if is_interactive:
            layout = "Dynamic Interactive Canvas & Animation Loop Architecture"
            subsystems = [
                "Lifecycle Engine",
                "Canvas Renderer",
                "Player Entity & State Kinematics",
                "Obstacle Spawner & Procedural Generation",
                "Collision Boundary & AABB Invariant",
                "Score & HUD",
                "Controls Dispatcher",
            ]
            components = [
                BlueprintComponent(
                    id="comp_01_loop",
                    name="Game Loop & Lifecycle Clock",
                    subsystem="Lifecycle Engine",
                    purpose="Frame tick clock, state machine transitions, and timing synchronization.",
                    contract="init(), start(), loop(timestamp), stop()",
                    invariant_guard="Strict delta-time clamping preventing frame jumps.",
                ),
                BlueprintComponent(
                    id="comp_02_canvas",
                    name="Dynamic Canvas Renderer",
                    subsystem="Canvas Renderer",
                    purpose="Double-buffered 2D canvas drawing with viewport scaling and particle effects.",
                    contract="render(state), clear(), resize()",
                    dependencies=["comp_01_loop"],
                    invariant_guard="All draw operations strictly bounded within viewport dimensions.",
                ),
                BlueprintComponent(
                    id="comp_03_player",
                    name="Player Entity & State Kinematics",
                    subsystem="Player Entity & State Kinematics",
                    purpose="Position, velocity, acceleration, jump/action kinetics, and bounds.",
                    contract="updatePlayer(dt), action()",
                    dependencies=["comp_01_loop"],
                    invariant_guard="Coordinate invariants clamped to playable zone boundaries.",
                ),
                BlueprintComponent(
                    id="comp_04_obstacles",
                    name="Procedural Obstacle & Entity Spawner",
                    subsystem="Obstacle Spawner & Procedural Generation",
                    purpose="Spawns and manages procedural obstacles, items, and moving entities.",
                    contract="spawn(), updateObstacles(dt)",
                    dependencies=["comp_03_player"],
                    invariant_guard="Deterministic obstacle intervals with fair passability guarantee.",
                ),
                BlueprintComponent(
                    id="comp_05_collision",
                    name="AABB & Boundary Collision Detector",
                    subsystem="Collision Boundary & AABB Invariant",
                    purpose="Detects player intersections with obstacles, hazards, and track bounds.",
                    contract="checkCollisions(player, obstacles) -> bool",
                    dependencies=["comp_03_player", "comp_04_obstacles"],
                    invariant_guard="Immediate state transition to game-over on collision.",
                ),
                BlueprintComponent(
                    id="comp_06_hud",
                    name="Score, Stats & HUD Overlay",
                    subsystem="Score & HUD",
                    purpose="Renders score counters, level indicators, health bars, and restart modals.",
                    contract="updateScore(delta), renderHUD()",
                    dependencies=["comp_01_loop"],
                    invariant_guard="Monotonically non-decreasing score during session.",
                ),
                BlueprintComponent(
                    id="comp_07_controls",
                    name="Keyboard, Mouse & Touch Dispatcher",
                    subsystem="Controls Dispatcher",
                    purpose="Binds arrow keys, WASD, space, clicks, and touches with debouncing.",
                    contract="bindControls(), onAction(e)",
                    dependencies=["comp_03_player"],
                    invariant_guard="Debounced inputs preventing concurrent direction conflicting events.",
                ),
            ]
        elif is_math:
            layout = "High-Assurance Mathematical & Algorithmic Architecture"
            subsystems = [
                "Input Validation Gate",
                "Mathematical Core Engine",
                "Boundary Singularity Preemption",
                "Verification Harness",
            ]
            components = [
                BlueprintComponent(
                    id="comp_01_val",
                    name="Type & Domain Validation Contract",
                    subsystem="Input Validation Gate",
                    purpose="Validates types, numerical ranges, sign invariants, and input constraints.",
                    contract="validate_inputs(*args) -> bool",
                    invariant_guard="Rejects out-of-domain, non-numeric, or malformed inputs with explicit exceptions.",
                ),
                BlueprintComponent(
                    id="comp_02_sing",
                    name="Zero/Null Singularity Preemption",
                    subsystem="Boundary Singularity Preemption",
                    purpose="Guards against zero-division, underflow, overflow, and empty domains.",
                    contract="preempt_singularities(*args) -> Optional[Result]",
                    dependencies=["comp_01_val"],
                    invariant_guard="Zero-divisor returns explicit safe fallback or raises controlled ZeroDivisionError.",
                ),
                BlueprintComponent(
                    id="comp_03_algo",
                    name="Irreducible Mathematical Computation Core",
                    subsystem="Mathematical Core Engine",
                    purpose="Executes core computation with provable complexity bounds.",
                    contract="compute(*args) -> Result",
                    dependencies=["comp_02_sing"],
                    invariant_guard="Referential transparency and idempotence.",
                ),
                BlueprintComponent(
                    id="comp_04_audit",
                    name="Post-Condition & Assertion Verification",
                    subsystem="Verification Harness",
                    purpose="Verifies invariant output contracts, epsilon tolerances, and correctness.",
                    contract="assert_invariants(result) -> bool",
                    dependencies=["comp_03_algo"],
                    invariant_guard="Output strictly satisfies mathematical contract specifications.",
                ),
            ]
        else:
            layout = f"General Modular Architecture for {task_clean[:60]}"
            subsystems = [
                "Input Contract & State",
                "Core Algorithmic Pipeline",
                "Boundary & Security Guards",
                "Integration & Verification",
            ]
            components = [
                BlueprintComponent(
                    id="comp_01_contract",
                    name="Input Contract & Type Invariants",
                    subsystem="Input Contract & State",
                    purpose="Validates input specifications, enforces types, and initializes state.",
                    contract="validate_and_init(*args)",
                    invariant_guard="Strict schema and type assertion.",
                ),
                BlueprintComponent(
                    id="comp_02_pipeline",
                    name="Core Business Logic & Pipeline",
                    subsystem="Core Algorithmic Pipeline",
                    purpose="Executes primary task logic, data processing, and functional transformations.",
                    contract="execute_pipeline(state)",
                    dependencies=["comp_01_contract"],
                    invariant_guard="Idempotent, side-effect free processing.",
                ),
                BlueprintComponent(
                    id="comp_03_guards",
                    name="Boundary & Security Guard Invariants",
                    subsystem="Boundary & Security Guards",
                    purpose="Monitors boundary constraints, prevents resource exhaustion, and blocks injection.",
                    contract="enforce_guards(data)",
                    dependencies=["comp_02_pipeline"],
                    invariant_guard="Zero unsafe execution paths or unhandled boundary exceptions.",
                ),
                BlueprintComponent(
                    id="comp_04_tests",
                    name="Verification Suite & Self-Checking Harness",
                    subsystem="Integration & Verification",
                    purpose="Executes assertions, invariant checks, and unit tests.",
                    contract="verify_all() -> bool",
                    dependencies=["comp_03_guards"],
                    invariant_guard="Run defined assertions; coverage and residual risk must be reported.",
                ),
            ]

        return ArchitectureBlueprint(
            meta_task=task_clean,
            topology=topology_type,
            system_layout=layout,
            subsystems=subsystems,
            components=components,
            global_invariants=active_invariants,
        )

    def generate_refinement_blueprint(
        self,
        meta_task: str,
        current_code: str,
        audit: Any,
    ) -> ArchitectureBlueprint:
        issues = getattr(audit, "detected_issues", []) or []
        comps = [
            BlueprintComponent(
                id=f"refine_{idx + 1}",
                name=f"Repair Invariant: {iss[:40]}",
                subsystem="Audit Refinement",
                purpose=f"Eliminates defect: {iss}",
                contract="reconcile_and_harden()",
                invariant_guard="Run regression tests and report any remaining behavior changes.",
            )
            for idx, iss in enumerate(issues)
        ] or [
            BlueprintComponent(
                id="refine_01",
                name="Zero-Defect Code Hardening",
                subsystem="Audit Refinement",
                purpose="Harden boundary conditions and optimize syntax.",
                contract="reconcile_and_harden()",
                invariant_guard="Preserve required syntax/contracts; verify repairs with tests and review.",
            )
        ]
        return ArchitectureBlueprint(
            meta_task=f"Refinement for {meta_task}",
            topology="dual_framework_convergence",
            system_layout="Dynamic Defect Elimination & Refinement Architecture",
            subsystems=["Audit Refinement", "Boundary Enforcement"],
            components=comps,
            global_invariants=[f"Target finding for review: {iss}" for iss in issues]
            or ["No findings supplied; invariants remain unverified."],
        )
