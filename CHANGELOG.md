## Parallel execution, security, observability, and evaluation pass (2026-10-11)

- Reworked the dependency DAG executor to validate missing dependencies/cycles before work starts, schedule ready nodes concurrently, unblock descendants as dependencies finish, and block descendants after failures.
- Wired deep LOGOS leaf implementation through a leaf-only DAG so parent planning nodes are not incorrectly executed as code-generation tasks. Added task-level trace events and removed the silent `pass`-code fallback when leaf generation fails.
- Added append-only JSONL execution traces with timestamp/run/task metadata and conservative credential redaction.
- Made container mode the default for single-file Python/Node execution. Added Docker/Podman controls for no network, read-only root/source mount, dropped capabilities, no-new-privileges, PID/memory/CPU bounds, bounded retained output, timeout cleanup, and fail-closed behavior when runtime/images are missing.
- Added restricted multi-file Python/Node project execution: bounded source bundles, path traversal rejection, read-only project mount, no network, non-root UID, dropped capabilities, no-new-privileges, resource limits, bounded output, timeout cleanup, and no host fallback.
- Added concurrency-safe budget reservations and wired the standard crew to run Builder implementation concurrently with an independent Reviewer architecture/security audit; executable verification and merge remain ordered and the LLM audit remains advisory.
- Added `AuthorizedMCPClient` for outbound MCP calls using fully qualified allowlist entries, plus tests proving unauthorized calls never reach the transport. Deployments must wire external client calls through the adapter and bind approval to trusted UI consent.
- Added a live-provider routing benchmark CLI for frozen JSONL cases and verified operator-supplied prices. Missing provider usage is labelled estimated; incomplete/failed routes do not produce misleading cost-savings percentages.
- Added opt-in real-container integration tests and a dedicated Ubuntu CI job that pulls the Python image and tests non-root execution, read-only filesystems, network isolation, and multi-file imports.
- Added regression and end-to-end tests covering actual parallel overlap, DAG validation/failure propagation, permission enforcement, redaction, secure multi-file command construction, and reproducible evaluation reports.
- Added `docs/PRODUCTION_CAPABILITIES.md` to distinguish implemented controls from remaining live-provider, deployment-host, MCP integration, and adversarial-test gaps.

# Changelog

## Production hardening pass (2026-10-10)

- Reorganized the uploaded source into a conventional `agent_core/`, `server/`, `web/`, and `tests/` layout.
- Made server package imports side-effect-free and added a no-provider startup state that fails closed on generation.
- Added HTTP health endpoints, URL-safe query parsing, legacy route aliases, body-size validation, strict JSON parsing, static asset confinement, and security response headers.
- Confined project tree/read/write operations to the active workspace and rejected traversal/symlink escapes.
- Routed editor writes through atomic workspace writes and timestamped backups.
- Added in-process synchronization for workspace lock state and unique atomic lock-registry persistence.
- Capped model output generation, including local OpenAI-compatible endpoints and Ollama.
- Corrected misleading reasoning status text: heuristic scores are not proofs, model confidence is not calibrated probability, and Qdit ranking is explicitly classical/unverified.
- Isolated artifact previews from the parent origin and added the premium command palette, toast region, focus styling, responsive layout refinements, and reduced-motion handling.
- Added regression tests for startup, API contracts, path boundaries, symlinks, concurrent lock acquisition, budget limits, preview isolation, and reasoning integrity.

## Utility windows and performance pass

- Added additive floating windows for multi-model routing/token-saving, live crew controls, system Control Room, and MCP server generation/testing. Existing main workspace/view markup remains intact.
- MCP Studio can generate a FastMCP server and client config, test a tool handler, and save `agentufo_mcp/server.py` plus `agentufo_mcp/client_config.json` into the active project.
- Normal conversational turns now use a fast path by default: one bounded model request, shorter bounded context, no automatic pondering/topology/decomposition passes, no default web search, and no routine holographic-memory write. Deep LOGOS analysis is opt-in through explicit deep-analysis wording.
- Capped retained chat history, added a short-lived runtime-status cache, reduced cockpit polling frequency, throttled high-frequency progress persistence, compacted the run ledger, and cached seat telemetry during progress callbacks.
- Bounded workspace fingerprint scanning by directory count/depth and skipped one redundant test-generation call when a project test suite is already present.

Limitations: this does not implement a multi-file parallel DAG execution strategy. The verified coordinator remains a serial Architect → Builder → Reviewer pipeline; split-work modes remain explicitly unavailable. The MCP window scaffolds/tests an MCP server and writes client configuration, but does not silently start arbitrary third-party MCP processes.

## Chat-first UI restoration

- Restored a first-class conversational chat as the default landing surface while retaining the existing Control Room, Model Routing, Live Crew, and MCP Studio utility windows.
- Added active chat model selection, streaming normal-chat responses, bounded browser-side conversation history, and plain-English routing of build prompts into the existing crew pipeline.
- Added an inline mission card in chat with crew status, progress, seat token telemetry, and shortcuts to edit, pause/resume, stop, or open the detailed Live Crew window.
- Kept the existing backend API contracts and utility-window workflows additive; the chat layer does not claim variable-size parallel workers that the current pipeline does not implement.
