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
