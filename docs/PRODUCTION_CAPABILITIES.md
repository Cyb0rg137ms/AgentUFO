# Production hardening: capabilities and limits

This document describes the hardening primitives added to AgentUFO. It intentionally separates implemented behavior from deployment guarantees that depend on infrastructure.

## Parallel task execution

`agent_core.logos.dag_executor` validates dependency graphs before executing work, rejects missing dependencies and cycles, schedules ready nodes concurrently up to `max_workers`, releases dependent work as soon as prerequisites finish, and blocks downstream nodes when a dependency fails. Optional `ExecutionTrace` events record task starts, completions, failures, and blocked states.

The standard Architect → Builder → Reviewer workflow now overlaps two independent model tasks after planning: the Builder generates the implementation while the Reviewer performs an advisory architecture/security audit. A concurrency-safe token/cost reservation protects the shared run budget. The audit is advisory; it cannot override the deterministic test/review gate. The final execution/verification and merge decision remain ordered. Parallelism does not make shared filesystem writes safe: concurrent builders must use separate paths or workspace locks.

## Execution traces

`ExecutionTrace` appends JSONL events with timestamps and run/task identifiers. It redacts common credential fields and token-like strings, bounds strings, and flushes each event. Treat traces as operational data: access-control the directory, set retention, and avoid putting full prompts or source contents into event fields. Redaction is a defense-in-depth filter, not a guarantee that every possible secret format is removed.

## Container-isolated execution

Container mode is the default for `CodeExecutionSandbox.execute_script`; `CodeExecutionSandbox.execute_untrusted_container` is also available explicitly for untrusted Python/Node snippets. It requires an already available Docker or Podman runtime and a locally present image; it does not pull images or fall back to host execution. It configures no network, read-only root filesystem, dropped Linux capabilities, `no-new-privileges`, PID/memory/CPU limits, a read-only source mount, and a small no-exec temporary filesystem.

**Important:** `mode="host"` or `AGENTUFO_SANDBOX_MODE=host` is a development/test-only compatibility path and is not a security boundary. Multi-file Python/Node projects are staged into a fresh directory and mounted read-only in the same restricted container profile; traversal, absolute paths, malformed bundles, excessive file counts, and oversized projects are rejected. Dependency installation and network access are intentionally not performed. Container invocation tests cover security flags using a fake runtime, and an opt-in integration test exercises a real container when Docker/Podman and the pre-pulled Python image are available. This environment did not have Docker/Podman, so real kernel/container isolation has not been independently validated here. Before production deployment, run the integration test on each supported OS and add adversarial tests for process-tree cleanup, output floods, filesystem escapes, and resource exhaustion. Container security depends on a patched runtime and host configuration.

## Filesystem and MCP permissions

`WorkspacePolicy` enforces workspace-root confinement, rejects traversal and symlink escapes, blocks common secret/key paths, and defaults to no delete capability. `MCPToolPolicy` uses a default-deny allowlist, explicit denials, and approval requirements for mutating or high-risk capabilities.

These are application-layer checks. They do not replace OS ACLs, process isolation, provider-level MCP permissions, or approval UX. `AuthorizedMCPDispatcher` gates local registered handlers; `AuthorizedMCPClient` is the outbound-client adapter and requires a fully qualified `server:tool` allowlist key. External MCP calls must go through this adapter, and the trusted UI—not model-generated arguments—must control the approval signal. A policy object that is never consulted is not an enforcement boundary. AgentUFO does not currently ship an automatically connected arbitrary external MCP client, so integrations added by deployments must explicitly wire the adapter.

## Reproducible model-routing benchmarks

`RoutingBenchmark` runs frozen cases against explicitly supplied route functions and writes a JSON report with a dataset hash, evaluator results, token/cost totals, latency summaries, and errors. Unlabelled cases no longer count as correct by default; cost-savings comparisons are withheld if a route has failed calls or incomplete cost observations. `scripts/run_routing_benchmark.py` can execute real configured providers from a JSONL case set and route configuration. Operators must enter verified current prices. When provider token counts are absent, the report explicitly marks the character-based token estimate. No live provider comparison or savings result is claimed until the operator runs that script against a frozen, representative task set. Exact-match evaluation is only a starter metric; code tasks need task-specific tests and acceptance criteria.

## Task completion and verification evaluation

`task_evaluation` reads frozen JSONL cases requiring `task_id`, `prompt`, and `acceptance`. It reports acceptance separately from verification and records a dataset hash. A result is only as meaningful as the caller's actual verification procedure; set `verified=true` only when real checks were executed and their results inspected.

## Validation

Run locally:

```bash
python -m unittest discover -s tests -v
python -m compileall -q agent_core server scripts run_web_ui.py
node --check web/js/app.js
# Optional: real container integration (requires Docker/Podman + python:3.12-alpine)
python -m unittest discover -s tests -p 'test_container_integration.py' -v
# Real provider benchmark (operator supplies frozen cases + verified prices)
python scripts/run_routing_benchmark.py --cases benchmarks/cases.jsonl --routes benchmarks/routes.example.json
```

CI runs the unit/contract suite and package build across its configured OS/Python matrix. Live-provider integration, Docker/Podman adversarial isolation, and end-to-end UI automation are not implied by a passing unit suite; configure and run those in a controlled integration environment before production deployment.
