# AgentUFO hardening build — validation report

**Validation run:** 2026-10-11

## Local checks

- Python compilation: passed (`python -m compileall -q agent_core server scripts tests run_web_ui.py`).
- Unit/contract suite: **68 passed, 2 skipped** (`python -m unittest discover -s tests -v`).
- Frontend JavaScript syntax: passed (`node --check web/js/app.js`).
- Routing benchmark CLI argument/help path: passed.
- Wheel build: passed with the environment's installed build tooling (`pip wheel . --no-deps --no-build-isolation`).

## Why two tests were skipped

The optional real-container integration tests were skipped because Docker/Podman is not installed in the current execution environment. A dedicated Ubuntu CI job now pulls `python:3.12-alpine` and exercises actual non-root execution, read-only source/root filesystem behavior, disabled network access, and multi-file Python imports when CI runs. Those container tests have **not** been observed passing in this local run.

## Implemented in this build

- The standard crew runs Builder generation concurrently with an independent Reviewer architecture/security audit. The shared token/cost budget uses reservations under a lock. The LLM audit is advisory; executable tests and the review/merge gate remain authoritative.
- Multi-file Python/Node project execution stages bounded source files and invokes a restricted container with networking disabled, read-only mounts/root, dropped capabilities, no-new-privileges, non-root UID, resource limits, and bounded retained output. Missing runtime/images fail closed.
- Workspace bundle validation rejects traversal and bounds file count and total content size, including the explicit development-host path.
- `AuthorizedMCPClient` gates outbound calls on a fully qualified `server:tool` allowlist; approval-required operations cannot bypass approval via a qualified tool name.
- Live-provider routing benchmark CLI consumes a frozen JSONL dataset and operator-supplied route/pricing configuration. Unlabelled exact-match cases do not pass automatically, and cost savings are withheld when observations are incomplete.

## Not claimed by this report

- Real container isolation has not been exercised in this local environment.
- No live provider benchmark or measured cost-saving percentage was produced.
- The MCP adapter must be wired into any external MCP client integration, and approval signals must originate from trusted user consent.
- General parallel multi-file code generation with same-file merge/conflict resolution is not claimed. The parallel crew audit and existing dependency-aware DAG execution are implemented, but provider-specific stress testing remains necessary.
- These checks do not constitute a security certification or proof that arbitrary generated code is safe.
