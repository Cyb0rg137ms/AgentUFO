"""Reproducible model-routing benchmark primitives; no provider calls are made implicitly."""
from __future__ import annotations
import hashlib, json, math, statistics, time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    prompt: str
    expected: Optional[str] = None
    tags: tuple[str, ...] = ()

@dataclass
class BenchmarkResult:
    case_id: str
    route: str
    passed: bool
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Optional[float] = None
    error: Optional[str] = None
    usage_estimated: bool = False
    cost_source: str = "configured_price_table"

    def to_dict(self) -> dict[str, Any]: return asdict(self)

class RoutingBenchmark:
    """Runs the same frozen cases through named routes and writes a reproducible report.

    ``runner`` must be supplied by the caller and returns a mapping with ``output`` and
    optional usage/cost fields. Correctness is exact-match when expected is provided;
    richer task-specific evaluators can be passed explicitly.
    """
    def __init__(self, evaluator: Optional[Callable[[BenchmarkCase, str], bool]] = None) -> None:
        # Unlabelled cases are not silently treated as correct. Supply an explicit
        # evaluator for task-specific scoring or an expected output for exact match.
        self.evaluator = evaluator or (lambda case, output: case.expected is not None and output.strip() == case.expected.strip())

    def run(self, cases: Iterable[BenchmarkCase], routes: Mapping[str, Callable[[str], Mapping[str, Any]]],
            report_path: str | Path) -> dict[str, Any]:
        frozen = list(cases)
        if not frozen or not routes: raise ValueError("At least one case and one route are required")
        if len({c.case_id for c in frozen}) != len(frozen): raise ValueError("case_id values must be unique")
        dataset_hash = hashlib.sha256("\n".join(json.dumps(asdict(c), sort_keys=True) for c in frozen).encode()).hexdigest()
        results: list[BenchmarkResult] = []
        for route_name, runner in routes.items():
            for case in frozen:
                start = time.perf_counter()
                try:
                    response = runner(case.prompt)
                    latency = (time.perf_counter() - start) * 1000
                    output = str(response.get("output", ""))
                    passed = bool(self.evaluator(case, output))
                    results.append(BenchmarkResult(case.case_id, route_name, passed, latency,
                        int(response.get("input_tokens", 0)), int(response.get("output_tokens", 0)),
                        (float(response["cost_usd"]) if response.get("cost_usd") is not None else None), None,
                        bool(response.get("usage_estimated", False)),
                        str(response.get("cost_source", "configured_price_table"))))
                except Exception as exc:
                    results.append(BenchmarkResult(case.case_id, route_name, False,
                        (time.perf_counter() - start) * 1000, error=f"{type(exc).__name__}: {exc}",
                        cost_source="unavailable_due_to_error"))
        summary = {}
        for route in routes:
            group = [r for r in results if r.route == route]
            latencies = [r.latency_ms for r in group]
            known_costs = [r.cost_usd for r in group if r.cost_usd is not None]
            cost_complete = len(known_costs) == len(group) and not any(r.error for r in group)
            summary[route] = {"cases": len(group), "pass_rate": sum(r.passed for r in group) / len(group),
                "total_cost_usd": (round(sum(known_costs), 8) if cost_complete else None),
                "known_cost_subtotal_usd": round(sum(known_costs), 8),
                "cost_complete": cost_complete,
                "cost_observation_count": len(known_costs),
                "total_input_tokens": sum(r.input_tokens for r in group),
                "total_output_tokens": sum(r.output_tokens for r in group),
                "mean_latency_ms": round(statistics.mean(latencies), 3),
                "p50_latency_ms": round(statistics.median(latencies), 3),
                "p95_latency_ms": round(sorted(latencies)[min(len(latencies)-1, max(0, math.ceil(.95*len(latencies))-1))], 3),
                "errors": sum(bool(r.error) for r in group),
                "usage_estimated_cases": sum(r.usage_estimated for r in group),
                "cost_source": sorted({r.cost_source for r in group})}
        baseline = next(iter(routes))
        comparisons = {}
        for route in routes:
            if route == baseline:
                continue
            base = summary[baseline]; candidate = summary[route]
            comparisons[route] = {
                "baseline_route": baseline,
                "pass_rate_delta": round(candidate["pass_rate"] - base["pass_rate"], 6),
                "cost_savings_fraction": (round(1 - candidate["total_cost_usd"] / base["total_cost_usd"], 6)
                                          if base["cost_complete"] and candidate["cost_complete"] and base["total_cost_usd"] > 0 else None),
                "mean_latency_delta_ms": round(candidate["mean_latency_ms"] - base["mean_latency_ms"], 3),
                "warning": "A cheaper route is not better if task quality falls; interpret metrics together."
            }
        report = {"schema_version": 1, "dataset_sha256": dataset_hash, "case_count": len(frozen),
                  "route_names": list(routes), "baseline_route": baseline,
                  "summary": summary, "comparisons": comparisons,
                  "results": [r.to_dict() for r in results],
                  "notes": "Scores reflect the supplied evaluator and runner; they are not a universal model-quality claim."}
        path = Path(report_path); path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(path)
        return report
