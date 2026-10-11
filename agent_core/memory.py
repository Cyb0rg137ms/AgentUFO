"""
memory.py
=========
Conversation memory system for the Agent-IDE Sandbox.

Implements two memory layers:
  1. ConversationBuffer — rolling context window with token-budget truncation
  2. EpisodicStore — task-level episodic memory with cosine-similarity retrieval

The memory system enables:
  - Long-horizon multi-turn conversations without context explosion
  - Retrieval of relevant past task solutions for similar new tasks
  - Automatic compression of old context to summary strings

Memory is a critical differentiator for production-grade agents:
  without it, every interaction starts from scratch.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Message model
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """A single message in the conversation."""
    role: str        # "user", "assistant", "tool", "system"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def token_estimate(self) -> int:
        """Rough token count estimate (4 chars ≈ 1 token)."""
        return max(1, len(self.content) // 4)

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


# ---------------------------------------------------------------------------
# ConversationBuffer
# ---------------------------------------------------------------------------

class ConversationBuffer:
    """
    Rolling conversation history with automatic token-budget management.

    When the buffer exceeds max_tokens, oldest non-system messages are
    dropped first. A system message (if present) is always preserved.

    This mimics the sliding window approach used by production LLM APIs.
    """

    def __init__(
        self,
        max_tokens: int = 10_000_000,
        system_prompt: Optional[str] = None,
    ) -> None:
        self.max_tokens = max_tokens
        self._messages: List[Message] = []

        if system_prompt:
            self._messages.append(Message(role="system", content=system_prompt))

    def add(self, role: str, content: str, **metadata: Any) -> None:
        """Add a message to the buffer, then trim if over budget."""
        msg = Message(role=role, content=content, metadata=metadata)
        self._messages.append(msg)
        self._trim()

    def _trim(self) -> None:
        """Remove oldest non-system messages until within token budget."""
        while self._total_tokens() > self.max_tokens and len(self._messages) > 1:
            # Find first non-system message and remove it
            for i, msg in enumerate(self._messages):
                if msg.role != "system":
                    self._messages.pop(i)
                    break
            else:
                break  # Only system messages remain

    def _total_tokens(self) -> int:
        return sum(m.token_estimate() for m in self._messages)

    def get_messages(self) -> List[Dict[str, str]]:
        """Returns messages in OpenAI chat format."""
        return [m.to_dict() for m in self._messages]

    def get_last_n(self, n: int) -> List[Message]:
        return self._messages[-n:]

    def clear_except_system(self) -> None:
        """Clears all non-system messages (session reset)."""
        self._messages = [m for m in self._messages if m.role == "system"]

    @property
    def token_usage(self) -> int:
        return self._total_tokens()

    @property
    def message_count(self) -> int:
        return len(self._messages)

    def __repr__(self) -> str:
        return f"ConversationBuffer({self.message_count} messages, {self.token_usage} tokens)"


# ---------------------------------------------------------------------------
# EpisodicStore
# ---------------------------------------------------------------------------

@dataclass
class Episode:
    """A completed task episode stored in episodic memory."""
    task_description: str
    solution_summary: str
    tool_calls: List[Dict[str, Any]]
    success: bool
    timestamp: float = field(default_factory=time.time)
    embedding: Optional[List[float]] = None  # Simple bag-of-words embedding

    def compute_embedding(self) -> List[float]:
        """Compute a simple character-trigram frequency vector for similarity."""
        text = (self.task_description + " " + self.solution_summary).lower()
        # Build trigram frequency dict
        trigrams: Dict[str, int] = {}
        for i in range(len(text) - 2):
            tg = text[i:i+3]
            trigrams[tg] = trigrams.get(tg, 0) + 1

        # Project to fixed-size 256-dim vector via hashing
        dim = 256
        vec = [0.0] * dim
        for tg, count in trigrams.items():
            idx = hash(tg) % dim
            vec[idx] += float(count)

        # L2 normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]

        self.embedding = vec
        return vec


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0
    return dot / (norm_a * norm_b)


class EpisodicStore:
    """
    Task-level episodic memory with embedding-based retrieval.

    Stores completed task episodes and retrieves similar past episodes
    when given a new task description. Useful for:
      - Avoiding repeated tool-call patterns for similar tasks
      - Providing few-shot examples to the planner agent
      - Tracking success rates per task type
    """

    def __init__(self, max_episodes: int = 1000) -> None:
        self.max_episodes = max_episodes
        self._episodes: List[Episode] = []

    def store(
        self,
        task_description: str,
        solution_summary: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        success: bool = True,
    ) -> Episode:
        """Records a completed task episode."""
        ep = Episode(
            task_description=task_description,
            solution_summary=solution_summary,
            tool_calls=tool_calls or [],
            success=success,
        )
        ep.compute_embedding()
        self._episodes.append(ep)

        # Evict oldest if over capacity
        if len(self._episodes) > self.max_episodes:
            self._episodes.pop(0)

        return ep

    def retrieve_similar(
        self,
        query: str,
        top_k: int = 3,
        min_similarity: float = 0.1,
    ) -> List[Tuple[Episode, float]]:
        """
        Retrieves top-k most similar episodes to the query string.

        Args:
            query: Task description to search for.
            top_k: Number of results to return.
            min_similarity: Minimum cosine similarity threshold.

        Returns:
            List of (episode, similarity_score) pairs, sorted by descending similarity.
        """
        if not self._episodes:
            return []

        # Compute query embedding
        query_ep = Episode(
            task_description=query,
            solution_summary="",
            tool_calls=[],
            success=True,
        )
        query_vec = query_ep.compute_embedding()

        scored: List[Tuple[Episode, float]] = []
        for ep in self._episodes:
            if ep.embedding is None:
                ep.compute_embedding()
            sim = _cosine_similarity(query_vec, ep.embedding)
            if sim >= min_similarity:
                scored.append((ep, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def success_rate(self, predicate: Optional[str] = None) -> float:
        """
        Returns success rate over stored episodes.

        Args:
            predicate: Optional substring filter on task_description.
        """
        eps = self._episodes
        if predicate:
            eps = [e for e in eps if predicate.lower() in e.task_description.lower()]
        if not eps:
            return 0.0
        return sum(1 for e in eps if e.success) / len(eps)

    @property
    def episode_count(self) -> int:
        return len(self._episodes)

    def __repr__(self) -> str:
        return f"EpisodicStore({self.episode_count} episodes, success_rate={self.success_rate():.2%})"


# ---------------------------------------------------------------------------
# MultiLayerContinuousMemory
# ---------------------------------------------------------------------------

@dataclass
class ContinuousTaskChunk:
    """A single layer chunk in a long-horizon continuous multi-layer generation."""
    chunk_id: int
    stage: str
    input_spec: str
    output_code: str
    cumulative_state: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "stage": self.stage,
            "input_spec": self.input_spec,
            "output_code": self.output_code[:1000],
            "cumulative_state": self.cumulative_state,
            "timestamp": self.timestamp,
        }


class MultiLayerContinuousMemory:
    """
    Maintains unified unbroken memory across recursive multi-layer prompt breakdowns.
    As the LLM breaks down a task into layers, each layer's response is stored and
    sent back into subsequent prompts to improve continuity; coherence is not guaranteed and context is bounded.
    """

    def __init__(self) -> None:
        self.chunks: List[ContinuousTaskChunk] = []
        self.shared_symbols: Dict[str, str] = {}
        self.active_invariants: List[str] = []

    def record_chunk(
        self,
        stage: str,
        input_spec: str,
        output_code: str,
        symbols: Optional[Dict[str, str]] = None,
        invariants: Optional[List[str]] = None,
    ) -> ContinuousTaskChunk:
        """Records an execution chunk and updates shared symbols/invariants."""
        if symbols:
            self.shared_symbols.update(symbols)
        if invariants:
            for inv in invariants:
                if inv not in self.active_invariants:
                    self.active_invariants.append(inv)

        chunk = ContinuousTaskChunk(
            chunk_id=len(self.chunks) + 1,
            stage=stage,
            input_spec=input_spec,
            output_code=output_code,
            cumulative_state=dict(self.shared_symbols),
        )
        self.chunks.append(chunk)
        return chunk

    def add_chunk(
        self,
        layer_index: int,
        stage: str,
        content: str,
        symbols: Optional[Dict[str, str]] = None,
        invariants: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ContinuousTaskChunk:
        """Alias for record_chunk supporting layer_index and content."""
        return self.record_chunk(
            stage=stage,
            input_spec=f"Layer {layer_index}: {stage}",
            output_code=content,
            symbols=symbols,
            invariants=invariants,
        )

    def compile_cumulative_prompt_prefix(self, max_tokens: int = 10_000_000) -> str:
        """
        Compiles all prior chunks and active invariants into a coherent prompt prefix
        to inject into the LLM before generating the next layer chunk.
        """
        if not self.chunks:
            return ""

        sections = [
            "=== CONTINUOUS TASK MEMORY: PRIOR STAGES & SYMBOL CONTRACTS ===",
            "You are working on a continuous multi-layer software task.",
            "Maintain complete syntactic and logical continuity with these completed stages:\n"
        ]

        for c in self.chunks:
            sections.append(f"--- [Completed Stage {c.chunk_id}: {c.stage}] ---")
            sections.append(f"Spec: {c.input_spec.strip()}")
            sections.append(f"Output / Code Fragment:\n{c.output_code.strip()}")
            sections.append("")

        if self.active_invariants:
            sections.append("Binding Invariants & Preconditions:")
            for inv in self.active_invariants:
                sections.append(f"  * {inv}")
            sections.append("")

        sections.append("=== END PRIOR STAGE CONTEXT. CONTINUE EXACTLY WHERE WE LEFT OFF. ===\n")
        return "\n".join(sections)

    def clear(self) -> None:
        self.chunks.clear()
        self.shared_symbols.clear()
        self.active_invariants.clear()

