# AgentUFO Control Room redesign report

Date: 2026-10-10

## What changed in this revision

### Product/UI

- Replaced the old interface with a new local-first Control Room: mission composer, run status, per-seat activity, token telemetry, task controls, event timeline, multi-model selection, Ollama connection/pull progress, filesystem browser/editor, and system health view.
- Added minimizable event tray, responsive layouts, keyboard shortcut `Ctrl/Cmd+K` for model routing, `Ctrl/Cmd+Enter` to launch a task, visible loading/error states, and reduced-motion support.
- Removed the external Google Fonts import so the interface has no required third-party font request.
- Token counters are explicitly marked estimated until usage is reported by the provider/coordinator. No token-by-token streaming claim is made for synchronous provider clients.

### Runtime / model routing

- Added a durable run ledger under `.agentufo/runtime/runs.json`, run events, run restart records, and interrupted-run recovery labels.
- Added REST endpoints for creating runs, polling runs/events, and pause/resume/stop/edit actions.
- Selected provider/model pairs are applied to actual coordinator seat bindings. Explicit assignments fail when unavailable or vetoed; the coordinator does not silently swap to another model.
- Save-token mode now fails closed when a local model is unavailable or vetoed rather than automatically spending cloud tokens.
- Added planner, total, and per-seat token limits. Provider usage is recorded when available; fallback estimates are labelled as estimates. Dollar spend remains an estimate based on configured rates, not invoice-grade billing.
- Added bounded workspace context to the architect prompt, existing target-file content to the builder prompt, target-path validation, durable shared-context injection, and filesystem change detection at agent checkpoints.
- Added an isolated temporary project-test overlay that applies the candidate file before running project tests. The live repository is not modified during test execution.
- Made NumPy optional for application startup. The Qdit experiment uses a clearly labelled classical ranking fallback when NumPy is unavailable; tensor-engine APIs report the missing optional dependency explicitly.
- Removed the simulated LLM provider and deterministic fake provider responses from the shipped project tree.

## Validation performed for this revision

- `python -m compileall -q agent_core server run_web_ui.py` — passed.
- `python -m unittest discover -s tests -v` — 34 tests passed.
- `node --check web/js/app.js` — passed.
- Import smoke test with NumPy deliberately unavailable — passed; server initialization reported `unconfigured` and the optional Qdit path reported `OPTIONAL_ACCELERATOR_UNAVAILABLE`.
- Live local HTTP smoke test — `/healthz`, `/`, `/api/crew/runs`, `/api/models`, and empty-task rejection behaved as expected.
- Candidate-overlay regression test — project tests passed with the staged candidate and failed with the old live file, while the live workspace file remained unchanged.
- ZIP archive integrity is checked at packaging time.

## Known release blockers (not hidden)

1. The synchronous provider interface cannot cancel a network request already in flight. Pause/stop takes effect at the next cooperative checkpoint; transport-level cancellation needs streaming/cancellable provider clients.
2. The current execution runner has timeouts and temporary directories but is **not** an OS-enforced sandbox. It does not guarantee networking is disabled or provide hard CPU/RAM/process isolation. Do not run untrusted code without container/VM isolation.
3. This remains a single-user local app. Authentication/CSRF hardening is required before non-loopback exposure.
4. Run coordination uses one active mission per server process and an atomic JSON ledger; it is not a multi-process transactional database.
5. The current verified workflow is Architect → Builder → Reviewer Gate. True multi-file split-work scheduling and competitive parallel proposal execution are not yet release-verified and must not be represented as fully supported. The UI must not imply these modes are active until implemented and tested.
6. Token-by-token live streaming, provider-specific price tables, and invoice-grade spend enforcement are not implemented.
7. Real-provider and browser E2E tests across Windows/macOS/Linux, accessibility testing, concurrency/TOCTOU tests, and an independent security review remain required.

A numeric “9/10” or “10/10” claim would be misleading until those blockers have reproducible evidence and external review.
