#!/usr/bin/env python3
"""Run a frozen JSONL task set against real configured AgentUFO model providers.

Routes JSON schema:
{
  "routes": [
    {"name":"local", "provider":"ollama", "model":"qwen2.5-coder:7b",
     "price_input_per_million":0, "price_output_per_million":0},
    {"name":"cloud", "provider":"openai", "model":"gpt-4o-mini",
     "price_input_per_million":0.15, "price_output_per_million":0.60}
  ]
}
Prices must be verified and entered by the operator; this script does not guess current prices.
If the provider omits token usage, a clearly marked chars/4 estimate is used for reporting.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Permit `python scripts/run_routing_benchmark.py` from a source checkout.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from agent_core.llm.registry import LLMRegistry
from agent_core.routing_benchmark import BenchmarkCase, RoutingBenchmark


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        required=True,
        help="Frozen JSONL cases: case_id, prompt, expected, optional tags",
    )
    parser.add_argument(
        "--routes", required=True, help="JSON file defining providers, models, and verified prices"
    )
    parser.add_argument("--report", default=".agentufo/benchmarks/routing-report.json")
    parser.add_argument("--max-tokens", type=int, default=1024)
    args = parser.parse_args()
    if not 1 <= args.max_tokens <= 16384:
        parser.error("--max-tokens must be between 1 and 16384")
    cases: list[BenchmarkCase] = []
    with Path(args.cases).open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if (
                not isinstance(row.get("case_id"), str)
                or not row["case_id"].strip()
                or not isinstance(row.get("prompt"), str)
            ):
                parser.error(f"Invalid case schema at line {line_no}")
            expected = row.get("expected")
            if not isinstance(expected, str):
                parser.error(
                    f"Every exact-match benchmark case must provide string expected output at line {line_no}; use a custom evaluator for code/task benchmarks"
                )
            cases.append(
                BenchmarkCase(row["case_id"], row["prompt"], expected, tuple(row.get("tags", [])))
            )
    config = json.loads(Path(args.routes).read_text(encoding="utf-8"))
    route_specs = config.get("routes", [])
    if not route_specs or len({r.get("name") for r in route_specs}) != len(route_specs):
        parser.error("routes config must contain a non-empty list with unique names")
    runners = {}
    for spec in route_specs:
        name, provider_name, model = spec.get("name"), spec.get("provider"), spec.get("model")
        if not all(isinstance(v, str) and v.strip() for v in (name, provider_name, model)):
            parser.error("Every route requires non-empty name, provider, and model")
        try:
            pin = float(spec["price_input_per_million"])
            pout = float(spec["price_output_per_million"])
        except (KeyError, TypeError, ValueError):
            parser.error(
                f"Route {name!r} must specify verified input/output prices per million tokens"
            )
        if pin < 0 or pout < 0:
            parser.error(f"Route {name!r} prices cannot be negative")
        provider = LLMRegistry.get_provider(provider_name, model)

        def make_runner(llm, input_price, output_price):
            def run(prompt: str) -> dict[str, Any]:
                response = llm.generate(prompt, max_tokens=args.max_tokens)
                input_tokens = int(getattr(response, "prompt_tokens", 0) or 0)
                output_tokens = int(getattr(response, "completion_tokens", 0) or 0)
                estimated = not (input_tokens > 0 and output_tokens > 0)
                if input_tokens <= 0:
                    input_tokens = max(1, len(prompt) // 4)
                if output_tokens <= 0:
                    output_tokens = max(1, len(str(getattr(response, "content", ""))) // 4)
                cost = (
                    input_tokens * input_price / 1_000_000
                    + output_tokens * output_price / 1_000_000
                )
                return {
                    "output": str(response.content),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost,
                    "usage_estimated": estimated,
                    "cost_source": "configured_prices; token counts estimated"
                    if estimated
                    else "configured_prices; provider token counts",
                }

            return run

        runners[name] = make_runner(provider, pin, pout)
    report = RoutingBenchmark().run(cases, runners, args.report)
    print(
        json.dumps(
            {
                "report_path": str(Path(args.report).resolve()),
                "dataset_sha256": report["dataset_sha256"],
                "summary": report["summary"],
                "comparisons": report["comparisons"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
