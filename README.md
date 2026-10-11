# AgentUFO Studio — hardening & product upgrade

AgentUFO is a local-first developer studio for model-assisted coding, multi-agent workflows, workspace
browsing, and review-gated code changes. This build organizes the supplied source into a runnable package
and adds an initial hardening and UX pass. It is a work-in-progress engineering baseline, not a claim of
enterprise certification or benchmark superiority.

## Quick start

Python 3.10+ is required. The local UI and HTTP API use the Python standard library; NumPy is optional for the experimental Qdit tensor module.

On Windows, the easiest path is to double-click `START_AGENTUFO.bat`. It creates the virtual environment if needed and launches Python directly without invoking `pip.exe` or requiring PowerShell activation policy changes.

```powershell
# Or run from the extracted project root
py -3 -m venv .venv
.\.venv\Scripts\python.exe run_web_ui.py
```

If Windows Application Control blocks the standalone `pip.exe`, do not call `pip` directly. Core startup needs no package install. For the optional Qdit experiment only, use `python -m pip install -e ".[qdit]"` if your system policy permits it.

Open `http://127.0.0.1:8000`. The server opens the browser by default. To keep it in the foreground without opening a tab, use `python run_web_ui.py --no-browser`. Keep the server bound to `127.0.0.1`; it has no login and is not designed for exposure to a network.

### Connect a model

- **Local:** open **Model routing**, start Ollama, choose an installed model, and connect it. Pull progress is shown when a model must be downloaded.
- **Cloud:** paste an API key in **Model routing**. AgentUFO detects the provider and proposes a default model; the UI clears the key field after submission. The provider may make a verification request.
- **Multiple models:** select several model/provider pairs and apply them. The run sends those assignments to the coordinator. When **Save API tokens** is enabled, the first selected cloud model is preferred for planning, local models are eligible for implementation, and the empirical capability probe can veto weak/unavailable local models. No silent fallback is intended.

Never commit `.env` or API keys.

### Secure code execution prerequisites

Single-file generated Python/Node code uses container mode by default. Install Docker Desktop (Windows/macOS) or Docker/Podman (Linux), start the daemon, and pre-pull the runtime images because AgentUFO deliberately does not pull images automatically during execution:

```bash
docker pull python:3.12-alpine
docker pull node:22-alpine
```

If no compatible container runtime/image is available, execution fails closed rather than silently running generated code on the host. Multi-file Python/Node projects are staged into a fresh directory and run with the same restricted container profile, mounted read-only. Network dependency installation is intentionally disabled. `AGENTUFO_SANDBOX_MODE=host` and `AGENTUFO_ALLOW_HOST_SHELL=1` are unsafe development-only opt-ins; do not enable them for untrusted prompts or production workloads. Real container isolation must be validated on the deployment host; the integration test runs in the Ubuntu CI container job when Docker is available.

## Control-room redesign

- **Live crew board:** run state, per-seat status, model binding, progress, token telemetry, and event timeline. Provider token counts are marked estimated until the provider/coordinator reports usage.
- **Per-run controls:** pause/resume, stop, shared instruction edits, per-seat stop/pause, and context injection. Synchronous provider calls already in flight may not be instantly interruptible; controls take effect at a safe checkpoint.
- **Multi-model routing:** selected models are passed into the coordinator's actual seat bindings. Local models are capability-probed and may be vetoed. A missing/unavailable selected model fails explicitly rather than silently consuming cloud tokens.
- **Real workspace:** bounded source context from the active project, path confinement, durable locks, staged file writes, atomic commit/rollback, file editor, and workspace-change context broadcast.
- **Review gate:** generated candidates are tested before commit. A failed gate rejects the candidate; the current pipeline permits one repair attempt for supported Python changes.
- **Durable runs:** run history and event timeline survive refresh and interrupted runs are labelled interrupted, not completed. The local JSON ledger is intended for one server process at a time.
- **Ollama setup:** start the daemon, inspect installed models, connect or pull a model, and show progress/errors rather than assuming it is available.
- **Budget controls:** total token, planner token, per-seat token, and estimated spend caps. Dollar spend is an estimate using the configured rate, not an invoice-grade price oracle.
- **No simulated LLM provider:** if a real provider is unavailable, generation fails with an actionable error.

## Test and quality commands

```bash
python -m unittest discover -s tests -v
python -m compileall -q agent_core server scripts tests run_web_ui.py
```

Optional development tools:

```bash
python -m pip install -e ".[dev,qdit]"
ruff check agent_core server run_web_ui.py tests
bandit -r agent_core server
```

Do not interpret passing syntax checks as proof that every workflow is correct. The production-capability tests include real thread-overlap assertions, dependency-failure propagation, path/MCP authorization, trace redaction, and deterministic report generation. A dedicated Ubuntu CI job pulls the Python runtime image and exercises actual container restrictions when that workflow runs. Live model-provider comparisons still require operator credentials and a frozen task set.

See [Production Capabilities](docs/PRODUCTION_CAPABILITIES.md) for implemented behavior, safe configuration, evaluation formats, and remaining integration gaps.

## Reproducible model-routing benchmark

The CLI runs a frozen JSONL task set through real configured providers. Copy the example files, replace the sample routes/models and **verify current prices** before benchmarking:

```bash
cp benchmarks/cases.example.jsonl benchmarks/cases.jsonl
cp benchmarks/routes.example.json benchmarks/routes.json
python scripts/run_routing_benchmark.py --cases benchmarks/cases.jsonl --routes benchmarks/routes.json --report .agentufo/benchmarks/report.json
```

Each case requires an exact expected output for the built-in evaluator. For code-generation comparisons, use task-specific tests and acceptance criteria instead of exact string matching. If a provider omits usage, token counts are estimated from characters and explicitly marked; cost savings are withheld when calls fail or cost observations are incomplete. The example dataset is only a smoke-test fixture, not a meaningful model leaderboard.

## Architecture map

- `agent_core/llm/`: provider contracts, Ollama, cloud APIs, provider registry.
- `agent_core/logos/`: decomposition, topology, memory, pondering, retrieval, audit, DAG, and Qdit modules.
- `agent_core/`: workspace manager, review gate, sandbox, tools, memory, operator, and crew orchestration.
- `server/`: API controller, HTTP routing, and starter artifacts.
- `web/`: frontend assets and the premium UX layer.

## Known limitations and honest status

- The host-mode execution runner is a development/test compatibility path, **not** a secure container boundary. Default container mode is restricted, but security still depends on the host kernel, container runtime, image provenance, and deployment configuration.
- This is a single-user local app; authentication and multi-tenant isolation are not implemented. Keep the server on loopback.
- Crew coordination is stateful and currently supports one active mission per server process. Run records are atomic JSON, not a multi-process transactional database.
- Pause/stop cannot terminate an already-running synchronous provider HTTP request. It stops at the next safe checkpoint; true transport-level cancellation needs streaming/cancellable provider clients.
- The standard crew pipeline keeps Architect planning first, then runs Builder implementation concurrently with an independent Reviewer architecture/security audit. Executable tests, repair, and merge remain ordered after those calls. The deep LOGOS path also has dependency-aware parallel execution for independent leaf tasks; concurrent multi-file builders still need live-provider stress tests and same-file conflict handling.
- **Code execution defaults to container mode** for Python/Node snippets and multi-file projects. Install Docker/Podman and pre-pull the required images. The container path uses no network, a read-only root/project mount, dropped capabilities, no-new-privileges, a non-root UID, and resource limits. Real isolation was not exercised in this development environment because Docker/Podman was unavailable; an opt-in integration test and dedicated CI job cover that boundary where a runtime is available. `AGENTUFO_SANDBOX_MODE=host` is an explicit development-only opt-in and is not safe for hostile code. Host shell access is disabled unless `AGENTUFO_ALLOW_HOST_SHELL=1` is explicitly set for local development.
- Workspace policy rejects traversal, symlink escapes, and common secret/key paths. The MCP policy supports default-deny authorization and approval hooks; outbound clients should use `AuthorizedMCPClient` with a fully qualified `server:tool` allowlist. AgentUFO does not automatically connect arbitrary external MCP servers, so deployments must wire this adapter into any external-client integration and bind approvals to trusted user consent.
- JSONL execution traces, redaction, a live-provider routing benchmark CLI, and a frozen task-evaluation harness are included. These are measurement infrastructure, not benchmark claims: use a representative versioned task set and verified provider prices before claiming cost savings. The real container integration test was skipped locally because no Docker/Podman runtime was available.
- The LOGOS pondering/decomposition/convergence scores are uncalibrated heuristics, not probabilities or proofs. NAS latency/success predictions remain unbenchmarked until measured. The Qdit module is a classical tensor-network experiment; its current candidate ranking is performed by a classical scoring function, not by a quantum computer. Holographic memory retains full records/chunks and is not constant-space compression.

See [SECURITY.md](SECURITY.md) before running generated code.

## Complete project bundle

This bundle includes `docs/ARCHITECTURE.md` (end-to-end flow), `docs/OPERATIONS.md`, a release acceptance checklist, a GitHub Actions CI workflow, local run scripts, and UI design references. The organized root modules are the canonical runtime; legacy simulated-provider code is not shipped.

For a clean local run, `scripts/run-local.ps1` supports Windows PowerShell and `scripts/run-local.sh` supports macOS/Linux. See `SECURITY.md` and the acceptance checklist before enabling code execution or exposing the HTTP server beyond localhost.

## Utility windows (latest update)

The existing main workspace remains in place. Top-bar utility windows provide:

- **Model routing** — select up to three model assignments and set token/spend caps for the verified three-seat pipeline.
- **Live crew** — inspect seat status and available pause/resume/stop/context controls.
- **Control room** — system/provider health and active-run summary.
- **MCP Studio** — generate a FastMCP server and client config, test the handler using container mode by default, and save the files to `agentufo_mcp/` in the active project. Host execution is available only as an explicit development opt-in.

To run a generated MCP server outside AgentUFO, install the optional SDK in the environment used by the MCP host: `python -m pip install -r requirements-mcp.txt`. If Windows Application Control blocks the package installer, use the approved package-management route for that machine. Review generated tool code before connecting it to a trusted host.

### Performance notes

Routine conversational prompts now skip the expensive LOGOS multi-pass analysis by default and use a bounded output/context budget. Include wording such as `analyze deeply` or `use LOGOS` to request the deeper path. Crew-run telemetry persistence and workspace fingerprint scanning are bounded/throttled. The standard crew now overlaps Builder implementation with an independent Reviewer architecture/security audit using concurrency-safe budget reservations; the authoritative test/review gate and merge decision remain sequential. The LOGOS path also supports validated DAG scheduling for independent leaf tasks. General parallel multi-file code generation still requires live-provider stress testing and same-file merge/conflict handling before it should be treated as a fully general parallel coding swarm.
