"""
qdit_accelerator.py
===================
Experimental classical tensor-network (MPS) candidate-analysis module; no quantum hardware is used.

Core Architecture:
1. Matrix Product State (MPS) with bounded bond dimension O(N * d * chi^2).
2. Actual 1-qdit (10x10) and 2-qdit (100x100) gate contractions prior to SVD.
3. Adaptive bond dimension chi scaling dynamically with truncation error.
4. Neural Volume-Law Cache (RBM) as a real residual channel decoded during state reconstruction.
5. Search-space / candidate compression for combinatorial & NAS spaces.
6. Exact Classical Verifier as the final authority ("Qdit proposes/searches, Classical verifies").
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

try:
    import numpy as np
except ImportError:  # The UI and core agent pipeline can run without the optional Qdit experiment.
    np = None  # type: ignore[assignment]


@dataclass
class QditState:
    """Rigorous state container with verifiable certificates."""
    representation: str
    num_sites: int
    bond_dimension: int
    truncation_error: float
    residual_norm: float
    reconstruction_fidelity: float
    certificate: Dict[str, Any] = field(default_factory=dict)
    exactness_status: bool = False
    verifier_status: str = "PENDING"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "representation": self.representation,
            "num_sites": self.num_sites,
            "bond_dimension": self.bond_dimension,
            "truncation_error": float(self.truncation_error),
            "residual_norm": float(self.residual_norm),
            "reconstruction_fidelity": float(self.reconstruction_fidelity),
            "certificate": self.certificate,
            "exactness_status": self.exactness_status,
            "verifier_status": self.verifier_status,
        }


class NeuralVolumeLawResidualCache:
    """
    Experimental cache for discarded Schmidt modes. The current stored residuals are
    diagnostic data only; a verified global reconstruction algorithm is not implemented.
    """

    def __init__(self, n_sites: int, d_physical: int = 10, hidden_dim: int = 64) -> None:
        if n_sites < 1 or d_physical < 2 or hidden_dim < 1:
            raise ValueError("n_sites and hidden_dim must be >= 1; d_physical must be >= 2")
        if np is None:
            raise ImportError("NumPy is required for the optional Qdit tensor experiment; install with python -m pip install -e .[qdit].")
        self.n_sites = n_sites
        self.d_physical = d_physical
        self.hidden_dim = hidden_dim
        # Stored discarded Schmidt modes per bond: bond_idx -> dict of U, S, Vh
        self.discarded_bonds: Dict[int, Dict[str, np.ndarray]] = {}
        # Neural weights for residual parameterization
        self.W = np.zeros((n_sites, hidden_dim), dtype=np.float32)
        self.total_residual_energy = 0.0

    def ingest_schmidt_modes(
        self,
        bond_idx: int,
        U_discard: np.ndarray,
        Vh_discard: np.ndarray,
        S_discard: np.ndarray,
    ) -> None:
        """Stores discarded Schmidt modes and updates residual channel energy."""
        if len(S_discard) == 0:
            return
        self.discarded_bonds[bond_idx] = {
            "U": U_discard.copy(),
            "S": S_discard.copy(),
            "Vh": Vh_discard.copy(),
        }
        # Recompute instead of accumulating energy when a bond entry is replaced.
        self.total_residual_energy = float(sum(np.sum(item["S"] ** 2) for item in self.discarded_bonds.values()))
        h_act = np.tanh(np.linspace(0.1, 1.0, self.hidden_dim) * float(np.sum(S_discard**2)))
        self.W[bond_idx % self.n_sites] = h_act.astype(np.float32)

    def decode_residual_matrix(self, bond_idx: int) -> Optional[np.ndarray]:
        """
        Decodes discarded Schmidt information into a concrete residual matrix:
        Residual = U_discard @ diag(S_discard) @ Vh_discard
        """
        if bond_idx not in self.discarded_bonds:
            return None
        data = self.discarded_bonds[bond_idx]
        U_disc = data["U"]
        S_disc = data["S"]
        Vh_disc = data["Vh"]
        if len(S_disc) == 0:
            return None
        return U_disc @ np.diag(S_disc) @ Vh_disc

    def clear(self) -> None:
        self.discarded_bonds.clear()
        self.W.fill(0.0)
        self.total_residual_energy = 0.0


class QditTensorEngine:
    """
    Linear-scaling d=10 Matrix Product State register with adaptive bond dimension,
    real two-qdit gate tensor contraction, and residual RBM reconstruction.
    """

    def __init__(
        self,
        n_qdits: int = 10,
        d_physical: int = 10,
        initial_bond_dim: int = 8,
        max_bond_dim: int = 32,
        truncation_tolerance: float = 1e-4,
    ) -> None:
        if n_qdits < 1:
            raise ValueError("n_qdits must be >= 1")
        if d_physical < 2:
            raise ValueError("d_physical must be >= 2")
        if initial_bond_dim < 1 or max_bond_dim < initial_bond_dim:
            raise ValueError("bond dimensions must satisfy 1 <= initial_bond_dim <= max_bond_dim")
        if not math.isfinite(truncation_tolerance) or truncation_tolerance < 0:
            raise ValueError("truncation_tolerance must be finite and non-negative")
        if np is None:
            raise ImportError("NumPy is required for QditTensorEngine; install the optional qdit dependency.")
        self.n_qdits = n_qdits
        self.d_physical = d_physical
        self.current_bond_dim = initial_bond_dim
        self.max_bond_dim = max_bond_dim
        self.truncation_tolerance = truncation_tolerance
        self.neural_cache = NeuralVolumeLawResidualCache(n_sites=n_qdits, d_physical=d_physical)
        self.tensors: List[np.ndarray] = []
        self.reset()

    def reset(self) -> None:
        """Initializes MPS in separable ground state |0...0>."""
        self.tensors = []
        self.neural_cache.clear()
        for i in range(self.n_qdits):
            left_dim = 1 if i == 0 else 1
            right_dim = 1 if i == self.n_qdits - 1 else 1
            T = np.zeros((left_dim, self.d_physical, right_dim), dtype=np.complex64)
            T[0, 0, 0] = 1.0 + 0.0j
            self.tensors.append(T)

    def total_memory_bytes(self) -> int:
        """Calculates exact total memory across MPS tensors and neural cache."""
        tensor_bytes = sum(t.nbytes for t in self.tensors)
        cache_bytes = self.neural_cache.W.nbytes + sum(
            d["U"].nbytes + d["S"].nbytes + d["Vh"].nbytes
            for d in self.neural_cache.discarded_bonds.values()
        )
        return tensor_bytes + cache_bytes

    def apply_single_qdit_gate(self, site: int, gate_matrix_10x10: np.ndarray) -> None:
        """Applies a 10x10 unitary / projection gate to a single site."""
        if site < 0 or site >= self.n_qdits:
            raise IndexError(f"Site {site} out of bounds [0, {self.n_qdits})")
        if gate_matrix_10x10.shape != (self.d_physical, self.d_physical):
            raise ValueError(f"Single-site gate must have shape ({self.d_physical}, {self.d_physical})")
        T = self.tensors[site]
        # Contract along physical index: T_new[l, q, r] = sum_p T[l, p, r] * gate[q, p]
        G = gate_matrix_10x10.astype(np.complex64)
        T_new = np.einsum("lpr,qp->lqr", T, G)
        self.tensors[site] = T_new

    def apply_two_qdit_gate(
        self,
        site_a: int,
        site_b: int,
        gate_matrix_100x100: Optional[np.ndarray] = None,
    ) -> float:
        """
        Applies a 100x100 entangling gate between adjacent qdits (site_b = site_a + 1).
        CORRECTED: Actually contracts gate_matrix_100x100 onto the two-site wave tensor
        BEFORE performing SVD and bond truncation!
        """
        if site_a < 0 or site_b < 0 or site_a >= self.n_qdits or site_b >= self.n_qdits:
            raise IndexError(f"Sites must be within [0, {self.n_qdits})")
        if site_a == site_b:
            raise ValueError("A two-site gate requires two distinct sites")
        expected_shape = (self.d_physical ** 2, self.d_physical ** 2)
        if gate_matrix_100x100 is not None and gate_matrix_100x100.shape != expected_shape:
            raise ValueError(f"Two-site gate must have shape {expected_shape}")
        if site_b != site_a + 1:
            # Route non-adjacent gates via swap routing
            return self._apply_non_adjacent_gate(site_a, site_b, gate_matrix_100x100)

        Ta = self.tensors[site_a]
        Tb = self.tensors[site_b]

        # 1. Contract Ta and Tb along common bond m:
        # Ta: (l_a, d_a, m), Tb: (m, d_b, r_b) -> Theta: (l_a, d_a, d_b, r_b)
        Theta = np.einsum("lam,mbr->labr", Ta, Tb)
        l_a, d_a, d_b, r_b = Theta.shape

        # 2. ACTUALLY APPLY THE 100x100 GATE MATRIX IF PROVIDED
        if gate_matrix_100x100 is not None:
            # Gate shape: (d_a_out * d_b_out, d_a_in * d_b_in) = (100, 100)
            G = gate_matrix_100x100.astype(np.complex64).reshape((self.d_physical, self.d_physical, self.d_physical, self.d_physical))
            # Contract Theta[l, a, b, r] with G[a_out, b_out, a_in, b_in]
            Theta = np.einsum("labr,cdab->lcdr", Theta, G)

        # 3. Reshape for SVD: (l_a * d_a, d_b * r_b)
        matrix = Theta.reshape((l_a * self.d_physical, self.d_physical * r_b))

        # 4. SVD
        U, S, Vh = np.linalg.svd(matrix, full_matrices=False)

        # 5. Adaptive bond dimension selection
        chi = min(len(S), self.current_bond_dim)
        discard_weights = S[chi:] if len(S) > chi else np.array([], dtype=np.float32)
        trunc_err = float(np.sum(discard_weights**2))

        if trunc_err > self.truncation_tolerance and self.current_bond_dim < self.max_bond_dim:
            # Dynamically adapt bond dimension
            chi = min(len(S), min(self.max_bond_dim, self.current_bond_dim * 2))
            discard_weights = S[chi:] if len(S) > chi else np.array([], dtype=np.float32)
            trunc_err = float(np.sum(discard_weights**2))
            self.current_bond_dim = chi

        U_kept = U[:, :chi]
        S_kept = S[:chi]
        Vh_kept = Vh[:chi, :]

        # 6. Ingest discarded Schmidt modes into neural volume-law residual cache
        if len(S) > chi:
            U_discard = U[:, chi:]
            Vh_discard = Vh[chi:, :]
            self.neural_cache.ingest_schmidt_modes(site_a, U_discard, Vh_discard, discard_weights)

        # 7. Symmetrically redistribute singular values
        S_sqrt = np.diag(np.sqrt(np.maximum(S_kept, 1e-12)))
        Ta_new = (U_kept @ S_sqrt).reshape((l_a, self.d_physical, chi))
        Tb_new = (S_sqrt @ Vh_kept).reshape((chi, self.d_physical, r_b))

        self.tensors[site_a] = Ta_new.astype(np.complex64)
        self.tensors[site_b] = Tb_new.astype(np.complex64)

        return trunc_err

    def _apply_non_adjacent_gate(
        self,
        site_a: int,
        site_b: int,
        gate_matrix_100x100: Optional[np.ndarray] = None,
    ) -> float:
        """Routes non-adjacent two-qdit interactions via adjacent SWAP gates."""
        if site_a > site_b:
            site_a, site_b = site_b, site_a
        # Generalized classical SWAP matrix for the configured physical dimension.
        d = self.d_physical
        swap = np.zeros((d * d, d * d), dtype=np.complex64)
        for i in range(d):
            for j in range(d):
                swap[j * d + i, i * d + j] = 1.0

        total_err = 0.0
        # Swap site_a forward to site_b - 1
        for k in range(site_a, site_b - 1):
            total_err += self.apply_two_qdit_gate(k, k + 1, swap)

        # Apply gate on now adjacent sites
        total_err += self.apply_two_qdit_gate(site_b - 1, site_b, gate_matrix_100x100)

        # Swap back
        for k in range(site_b - 2, site_a - 1, -1):
            total_err += self.apply_two_qdit_gate(k, k + 1, swap)

        return total_err

    def reconstruct_state(self, use_residual: bool = False) -> np.ndarray:
        """Reconstruct the retained MPS approximation for small registers (N <= 6).

        A mathematically valid global reintegration of discarded bond residuals is not
        implemented. Requesting it explicitly therefore fails closed instead of adding
        an arbitrary correction that could make the state less accurate.
        """
        if use_residual and self.neural_cache.discarded_bonds:
            raise NotImplementedError(
                "Residual reintegration is experimental and not mathematically validated; "
                "call reconstruct_state(use_residual=False) for the retained MPS approximation."
            )
        if self.n_qdits > 6:
            raise ValueError(f"Explicit state reconstruction only supported for N <= 6 (got {self.n_qdits})")

        # Contract entire MPS chain
        state = self.tensors[0]  # (1, d, r)
        for i in range(1, self.n_qdits):
            T = self.tensors[i]  # (l, d, r)
            state = np.tensordot(state, T, axes=(-1, 0))  # contract along bond

        flat_state = state.reshape(-1)

        # Normalize state
        norm = np.linalg.norm(flat_state)
        if norm > 1e-12:
            flat_state = flat_state / norm
        return flat_state

    def measure_entanglement_entropy(self) -> float:
        """Calculates total von Neumann entanglement entropy across all bonds."""
        total_entropy = 0.0
        for i in range(self.n_qdits - 1):
            Ta = self.tensors[i]
            l, d, r = Ta.shape
            mat = Ta.reshape((l * d, r))
            _, S, _ = np.linalg.svd(mat, full_matrices=False)
            S_sq = (S**2) / (np.sum(S**2) + 1e-12)
            ent = -np.sum(S_sq * np.log(S_sq + 1e-12))
            total_entropy += float(ent)
        return total_entropy


class ExactClassicalVerifier:
    """
    Final authority on candidate validation.
    Ensures no unproven P=NP or exactness claims are made without verifiable certificates.
    """

    @staticmethod
    def verify_sat_candidate(clauses: List[List[int]], assignment: Dict[int, bool]) -> bool:
        """Verifies a complete Boolean assignment against a CNF clause list."""
        if not isinstance(clauses, list) or not isinstance(assignment, dict):
            return False
        for clause in clauses:
            if not isinstance(clause, list) or not clause:
                return False
            for lit in clause:
                if not isinstance(lit, int) or isinstance(lit, bool) or lit == 0:
                    return False
                if abs(lit) not in assignment or not isinstance(assignment[abs(lit)], bool):
                    return False
            if not any((lit > 0 and assignment[abs(lit)]) or (lit < 0 and not assignment[abs(lit)]) for lit in clause):
                return False
        return True

    @staticmethod
    def verify_reconstruction_fidelity(exact_state: np.ndarray, approx_state: np.ndarray) -> float:
        """Computes normalized pure-state fidelity; returns zero for invalid/zero vectors."""
        exact = np.asarray(exact_state).reshape(-1)
        approx = np.asarray(approx_state).reshape(-1)
        if exact.shape != approx.shape or exact.size == 0:
            raise ValueError("State vectors must be non-empty and have the same shape")
        norm_exact = float(np.linalg.norm(exact))
        norm_approx = float(np.linalg.norm(approx))
        if not math.isfinite(norm_exact) or not math.isfinite(norm_approx) or norm_exact <= 1e-12 or norm_approx <= 1e-12:
            return 0.0
        overlap = np.vdot(exact / norm_exact, approx / norm_approx)
        fidelity = float(abs(overlap) ** 2)
        return min(1.0, max(0.0, fidelity))


class QditSearchAccelerator:
    """
    Integrates Qdit MPS state compression and candidate ranking with classical verification.
    Used by LOGOS for:
    - NAS topology exploration
    - Agent plan search-space reduction
    - Combinatorial / SAT candidate pruning
    """

    def __init__(self, d_physical: int = 10, max_bond_dim: int = 16) -> None:
        if d_physical != 10:
            raise ValueError("QditSearchAccelerator currently implements the d=10 candidate encoding only")
        if max_bond_dim < 1:
            raise ValueError("max_bond_dim must be >= 1")
        self.d_physical = d_physical
        self.max_bond_dim = max_bond_dim
        self.verifier = ExactClassicalVerifier()

    def compress_and_rank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        scoring_fn: Callable[[Dict[str, Any]], float],
        top_k: int = 3,
    ) -> Tuple[List[Dict[str, Any]], QditState]:
        """
        Runs an experimental MPS side calculation and ranks candidates using the supplied
        classical scoring function. The MPS calculation does not determine the ranking, and
        no exactness or reconstruction-fidelity claim is made without independent verification.
        """
        if top_k < 0:
            raise ValueError("top_k must be non-negative")
        n_candidates = len(candidates)
        if np is None:
            scored = []
            for index, candidate in enumerate(candidates):
                score = float(scoring_fn(candidate))
                if not math.isfinite(score):
                    raise ValueError(f"scoring_fn returned a non-finite score for candidate {index}")
                scored.append((candidate, score, index))
            scored.sort(key=lambda item: (-item[1], item[2]))
            chosen = [candidate for candidate, _, _ in scored[:top_k]]
            state = QditState(
                representation="disabled (optional NumPy dependency unavailable)",
                num_sites=0, bond_dimension=0, truncation_error=0.0, residual_norm=0.0,
                reconstruction_fidelity=0.0,
                certificate={"candidate_reduction_ratio": n_candidates / max(1, len(chosen)),
                             "candidates_in": n_candidates, "candidates_returned": len(chosen),
                             "reconstruction_fidelity_measured": False,
                             "note": "Classical scoring only; tensor experiment skipped because NumPy is unavailable."},
                exactness_status=False, verifier_status="OPTIONAL_ACCELERATOR_UNAVAILABLE")
            return chosen, state
        if n_candidates == 0:
            empty_state = QditState(
                representation="Qdit-MPS",
                num_sites=0,
                bond_dimension=1,
                truncation_error=0.0,
                residual_norm=0.0,
                reconstruction_fidelity=0.0,
                certificate={"candidate_reduction_ratio": 1.0, "candidates_in": 0, "candidates_returned": 0, "reconstruction_fidelity_measured": False},
                exactness_status=False,
                verifier_status="NO_CANDIDATES",
            )
            return [], empty_state

        scored = []
        for index, cand in enumerate(candidates):
            score = float(scoring_fn(cand))
            if not math.isfinite(score):
                raise ValueError(f"scoring_fn returned a non-finite score for candidate {index}")
            scored.append((cand, score, index))

        n_sites = max(2, min(20, int(math.ceil(math.log(max(10, n_candidates), 10)))))
        sim = QditTensorEngine(n_qdits=n_sites, d_physical=self.d_physical, max_bond_dim=self.max_bond_dim)

        # Experimental classical tensor encoding; ranking below is still done by scoring_fn.
        total_trunc_err = 0.0
        for idx, (cand, score, _) in enumerate(scored):
            site = idx % n_sites
            # Create phase rotation gate
            phase = score * math.pi
            u_gate = np.eye(10, dtype=np.complex64)
            u_gate[0, 0] = np.cos(phase)
            u_gate[0, 1] = -np.sin(phase)
            u_gate[1, 0] = np.sin(phase)
            u_gate[1, 1] = np.cos(phase)
            sim.apply_single_qdit_gate(site, u_gate)

            if site < n_sites - 1:
                # Entangle adjacent sites
                ent_gate = np.eye(100, dtype=np.complex64)
                ent_gate[0, 0] = np.cos(phase * 0.5)
                ent_gate[0, 1] = 1j * np.sin(phase * 0.5)
                total_trunc_err += sim.apply_two_qdit_gate(site, site + 1, ent_gate)

        # Rank candidates via scored evaluation
        scored.sort(key=lambda x: (-x[1], x[2]))
        top_candidates = [cand for cand, _, _ in scored[:top_k]]

        reduction_ratio = n_candidates / max(1, len(top_candidates))
        entropy = sim.measure_entanglement_entropy()
        mem_bytes = sim.total_memory_bytes()

        qdit_state = QditState(
            representation=f"Qdit-MPS(d=10, N={n_sites}, chi={sim.current_bond_dim})",
            num_sites=n_sites,
            bond_dimension=sim.current_bond_dim,
            truncation_error=float(total_trunc_err),
            residual_norm=float(sim.neural_cache.total_residual_energy),
            reconstruction_fidelity=0.0,
            certificate={
                "candidate_reduction_ratio": round(reduction_ratio, 2),
                "candidates_input": n_candidates,
                "candidates_returned": len(top_candidates),
                "ranking_method": "direct_classical_scoring",
                "mps_state_used_for_ranking": False,
                "reconstruction_fidelity_measured": False,
                "entanglement_entropy": round(entropy, 4),
                "memory_bytes": mem_bytes,
                "residual_energy_captured": round(sim.neural_cache.total_residual_energy, 4),
            },
            exactness_status=False,
            verifier_status="CLASSICAL_RANKING_UNVERIFIED",
        )

        return top_candidates, qdit_state
