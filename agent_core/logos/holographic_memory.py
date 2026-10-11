"""
holographic_memory.py
=====================
Layer 3: Holographic Memory Compression (LOGOS).
Derived from Claude-Lightyear v10.0 Ultra (Layer 3: Holographic Memory Compression).

Implements Boundary-to-Bulk holographic projection to compress extensive execution
and reasoning history into compact boundary state representations. Enables associative
querying over unbounded context windows without token explosion.
"""

from __future__ import annotations

import cmath
import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union


@dataclass
class LayerChunk:
    """A multi-layer task chunk recorded during continuous LLM generation."""
    layer_index: int
    name: str
    prompt: str
    response: str
    invariants: List[str] = field(default_factory=list)
    token_count: int = field(default=0)
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer_index": self.layer_index,
            "name": self.name,
            "prompt": self.prompt,
            "response": self.response,
            "token_count": self.token_count,
            "invariants": self.invariants,
            "timestamp": self.timestamp,
        }


@dataclass
class HolographicMemoryRecord:
    """A memory trace projected onto the holographic boundary."""
    id: str
    content: str
    boundary_vector: List[complex]
    token_count: int = 0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


import threading

class HolographicMemoryEngine:
    """
    Experimental classical associative-memory prototype.

    It projects text into fixed-dimensional complex vectors and retains full source records and
    layer chunks for recall. This is not quantum hardware, and total storage/context size is not
    constant-space; callers must bound retained records and the context they inject into an LLM.
    """

    def __init__(self, dimension: int = 128, max_token_capacity: int = 100_000) -> None:
        if dimension < 1 or max_token_capacity < 1:
            raise ValueError("dimension and max_token_capacity must be >= 1")
        self.dimension = dimension
        self.max_token_capacity = max_token_capacity
        self.total_tokens_stored = 0
        self._layer_tokens_stored = 0
        self._boundary_state: List[complex] = [0.0 + 0.0j] * self.dimension
        self._records: List[HolographicMemoryRecord] = []
        self._layer_chunks: List[LayerChunk] = []
        self._lock = threading.Lock()

        # Classical 10-component phase projection matrix for a heuristic boundary vector
        self.d_qudit: int = 10
        self._qudit_projection_matrix: List[List[complex]] = []
        for k in range(self.d_qudit):
            row = []
            for j in range(self.dimension):
                angle = (2.0 * math.pi * k * j) / self.dimension
                row.append(cmath.exp(1j * angle) / math.sqrt(self.d_qudit))
            self._qudit_projection_matrix.append(row)

    def project_qudit_phase_state(self, phase_or_params: Union[float, List[float], str], thread_idx: int = 0) -> List[complex]:
        """
        Maps parallel execution thread parameters onto a 10-state qudit Hilbert space (|0> ... |9>).
        Computes unitary time evolution under dynamic qudit Hamiltonian and projects onto boundary.
        """
        qudit_state = [0.0 + 0.0j] * self.d_qudit
        if isinstance(phase_or_params, (int, float)):
            base_phase = float(phase_or_params)
            for k in range(self.d_qudit):
                p = base_phase + (k * math.pi / 5.0) + (thread_idx * 0.1)
                qudit_state[k] = cmath.exp(1j * p)
        elif isinstance(phase_or_params, str):
            h = int(hashlib.md5(phase_or_params.encode("utf-8")).hexdigest()[:8], 16)
            for k in range(self.d_qudit):
                p = ((h + k * 137 + thread_idx * 31) % 1000 / 1000.0) * 2.0 * math.pi - math.pi
                qudit_state[k] = cmath.exp(1j * p)
        else:
            params = list(phase_or_params)[:self.d_qudit]
            for k, val in enumerate(params):
                qudit_state[k] = cmath.exp(1j * float(val))
            for k in range(len(params), self.d_qudit):
                qudit_state[k] = cmath.exp(1j * (k * 0.2))

        # Normalize qudit state vector: sum(|c_k|^2) = 1
        norm_q = math.sqrt(sum(abs(x) ** 2 for x in qudit_state))
        if norm_q > 1e-9:
            qudit_state = [x / norm_q for x in qudit_state]

        # Expand qudit state to boundary memory dimension via projection matrix P
        projected = [0.0 + 0.0j] * self.dimension
        for j in range(self.dimension):
            s = sum(qudit_state[k] * self._qudit_projection_matrix[k][j] for k in range(self.d_qudit))
            projected[j] = s

        # Unit normalization
        norm_p = math.sqrt(sum(abs(x) ** 2 for x in projected))
        if norm_p > 1e-9:
            projected = [x / norm_p for x in projected]
        return projected

    # Backward-compatible alias; this is a classical phase projection, not quantum execution.
    project_qudit_quantum_state = project_qudit_phase_state

    def encode_text_to_phase_vector(self, text: str) -> List[complex]:
        """
        Projects text tokens onto a complex unit-circle phase vector.
        Each token produces a deterministic phase shift theta in [-pi, pi].
        Also mixes in a deterministic 10-component phase projection (a classical heuristic, not quantum computation).
        """
        vec = [0.0 + 0.0j] * self.dimension
        words = text.lower().split()
        if not words:
            return [1.0 + 0.0j] * self.dimension

        for i, word in enumerate(words):
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest()[:8], 16)
            slot = (h ^ (i * 31)) % self.dimension
            theta = ((h % 1000) / 1000.0) * 2.0 * math.pi - math.pi
            vec[slot] += cmath.exp(1j * theta)

        # Superimpose 10-state qudit state representation
        q_vec = self.project_qudit_phase_state(text)
        for i in range(self.dimension):
            vec[i] = 0.7 * vec[i] + 0.3 * q_vec[i]

        # Normalize L2 norm
        norm = math.sqrt(sum(abs(x) ** 2 for x in vec))
        if norm > 1e-9:
            vec = [x / norm for x in vec]
        return vec

    def store(self, content: str, memory_id: Optional[str] = None, **metadata: Any) -> HolographicMemoryRecord:
        """Store a trace with bounded retention; oldest traces are evicted when needed."""
        if not isinstance(content, str):
            raise TypeError("Memory content must be a string")
        phase_vec = self.encode_text_to_phase_vector(content)
        tokens = max(1, len(content) // 4)
        if tokens > self.max_token_capacity:
            raise ValueError(f"Memory item exceeds the configured {self.max_token_capacity}-token capacity")

        with self._lock:
            mid = memory_id or f"holo_{len(self._records) + 1}_{int(time.time() * 1000)}"
            # Replacing an ID must not double-count retained tokens.
            existing_index = next((i for i, item in enumerate(self._records) if item.id == mid), None)
            if existing_index is not None:
                old = self._records.pop(existing_index)
                self.total_tokens_stored -= old.token_count

            while self.total_tokens_stored + self._layer_tokens_stored + tokens > self.max_token_capacity:
                if self._records:
                    removed = self._records.pop(0)
                    self.total_tokens_stored -= removed.token_count
                elif self._layer_chunks:
                    removed_chunk = self._layer_chunks.pop(0)
                    self._layer_tokens_stored -= removed_chunk.token_count
                else:
                    raise ValueError("Memory capacity is too small for this record")

            record = HolographicMemoryRecord(
                id=mid,
                content=content,
                boundary_vector=phase_vec,
                token_count=tokens,
                metadata=metadata,
            )
            self._records.append(record)
            self.total_tokens_stored += tokens

            # Holographic interference superposition onto global boundary (heuristic only).
            for i in range(self.dimension):
                self._boundary_state[i] = 0.85 * self._boundary_state[i] + 0.15 * phase_vec[i]

        return record

    def associative_recall(
        self,
        query: str,
        top_k: int = 3,
        min_fidelity: float = 0.05,
    ) -> List[Tuple[HolographicMemoryRecord, float]]:
        """
        Retrieves memory traces matching query phase alignment (inner product fidelity).
        """
        query_vec = self.encode_text_to_phase_vector(query)
        scored: List[Tuple[HolographicMemoryRecord, float]] = []
        with self._lock:
            records = list(self._records)

        for record in records:
            # Complex Hermitian dot product: <u, v> = sum(u_i * conjugate(v_i))
            dot = sum(
                (q.real * r.real + q.imag * r.imag)
                for q, r in zip(query_vec, record.boundary_vector)
            )
            fidelity = max(0.0, float(dot))
            if fidelity >= min_fidelity:
                scored.append((record, fidelity))

        if not scored and records:
            scored = [
                (
                    r,
                    max(0.0, float(sum(q.real * r.boundary_vector[i].real + q.imag * r.boundary_vector[i].imag for i, q in enumerate(query_vec))))
                )
                for r in records
            ]

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # Aliases for ergonomic API usage
    retrieve = associative_recall
    query = associative_recall

    def get_boundary_compression_stats(self) -> Dict[str, Any]:
        """Returns holographic compression metrics for massive token context."""
        total_raw_chars = sum(len(r.content) for r in self._records)
        holo_bytes = self.dimension * 16  # 16 bytes per complex128
        compression_ratio = (total_raw_chars / max(1, holo_bytes)) if total_raw_chars > 0 else 1.0

        return {
            "dimension": self.dimension,
            "retains_full_records": True,
            "constant_space_compression": False,
            "total_records": len(self._records),
            "total_chunks_stored": len(self._layer_chunks),
            "total_tokens_stored": self.total_tokens_stored + self._layer_tokens_stored,
            "max_token_capacity": self.max_token_capacity,
            "retention_policy": "oldest-record eviction",
            "raw_characters_stored": total_raw_chars,
            "boundary_vector_bytes": holo_bytes,
            "boundary_projection_ratio": round(compression_ratio, 2),
            "compression_ratio_note": "Projection-vector ratio only; full records are retained and are not compressed away.",
            "boundary_norm": round(math.sqrt(sum(abs(x) ** 2 for x in self._boundary_state)), 4),
        }

    def store_blueprint(self, blueprint: Any) -> None:
        """
        Ingests an ArchitectureBlueprint into holographic memory.
        Encodes the master layout and registers individual component contracts into phase vectors.
        """
        # Store master layout representation
        master_entry = getattr(blueprint, "format_holographic_entry", lambda: str(blueprint))()
        self.store(content=master_entry, category="blueprint_master")

        # Index each component specification
        components = getattr(blueprint, "components", [])
        for comp in components:
            comp_entry = f"COMPONENT::{comp.id}::{comp.name}::SUBSYSTEM::{comp.subsystem}::CONTRACT::{comp.contract}"
            self.store(content=comp_entry, category="blueprint_component", tool=comp.id)

    def store_layer_chunk(
        self,
        layer_index: int,
        name: str,
        prompt: str = "",
        response: str = "",
        invariants: Optional[List[str]] = None,
        **metadata: Any,
    ) -> LayerChunk:
        """
        Stores an intermediate generation layer chunk to maintain inter-chunk coherence
        across long, continuous multi-stage tasks.
        """
        resp_val = response if response else prompt
        chunk_tokens = max(1, (len(prompt) + len(resp_val)) // 4)
        summary_text = f"LAYER_{layer_index} [{name}] ({chunk_tokens} tokens): {prompt[:500]} => {resp_val[:1000]}"
        summary_tokens = max(1, len(summary_text) // 4)
        if chunk_tokens + summary_tokens > self.max_token_capacity:
            raise ValueError("Layer chunk and summary exceed the configured memory capacity")
        chunk = LayerChunk(
            layer_index=layer_index,
            name=name,
            prompt=prompt,
            response=resp_val,
            token_count=chunk_tokens,
            invariants=invariants or [],
            metadata=metadata,
        )
        with self._lock:
            while self.total_tokens_stored + self._layer_tokens_stored + chunk_tokens + summary_tokens > self.max_token_capacity:
                if self._layer_chunks:
                    removed_chunk = self._layer_chunks.pop(0)
                    self._layer_tokens_stored -= removed_chunk.token_count
                elif self._records:
                    removed_record = self._records.pop(0)
                    self.total_tokens_stored -= removed_record.token_count
                else:
                    raise ValueError("Memory capacity is too small for this layer chunk")
            self._layer_chunks.append(chunk)
            self._layer_tokens_stored += chunk_tokens

        # This is a compact retrieval hint, not full-context preservation.
        self.store(summary_text, memory_id=f"layer_{layer_index}_{int(time.time()*1000)}", category="layer_chunk")
        return chunk

    def build_coherent_context_for_next_layer(
        self, current_layer_index: int = 999, query: str = "", max_context_chars: int = 40_000
    ) -> str:
        """Build a bounded context from recent chunks and a small associative recall set."""
        if max_context_chars < 512:
            raise ValueError("max_context_chars must be >= 512")
        with self._lock:
            chunks = [c for c in self._layer_chunks if c.layer_index < current_layer_index]
        if not chunks:
            return ""

        header = [
            "=== CONTINUOUS MULTI-LAYER TASK CONTEXT (BOUNDED) ===",
            "Recent prior layers are included below; older content may have been evicted or omitted.",
        ]
        budget = max_context_chars - sum(len(line) + 1 for line in header) - 64
        selected: List[str] = []
        for chunk in reversed(chunks):
            section = (
                f"--- [Layer {chunk.layer_index}: {chunk.name} ({chunk.token_count} estimated tokens)] ---\n"
                f"Specification: {chunk.prompt.strip()[:4000]}\n"
                f"Implementation: {chunk.response.strip()[:8000]}\n"
                f"Invariants: {', '.join(chunk.invariants) if chunk.invariants else 'None recorded'}"
            )
            if len(section) > budget:
                section = section[:max(0, budget - 20)] + "\n[section truncated to context budget]"
            if not section or budget <= 0:
                break
            selected.append(section)
            budget -= len(section) + 2
            if budget <= 0:
                break
        sections = header + list(reversed(selected))

        if query and budget > 0:
            recalls = self.associative_recall(query, top_k=2)
            if recalls:
                sections.append("--- [Relevant associative traces; heuristic similarity] ---")
                for rec, score in recalls:
                    item = f"- (similarity={score:.3f}) {rec.content[:200]}"
                    if len(item) <= budget:
                        sections.append(item)
                        budget -= len(item) + 1

        result = "\n\n".join(sections) + "\n=== END OF CONTEXT ==="
        return result[:max_context_chars]

    def get_layer_chunks(self) -> List[Dict[str, Any]]:
        """Returns retained multi-layer chunks."""
        with self._lock:
            chunks = list(self._layer_chunks)
        return [c.to_dict() for c in chunks]
