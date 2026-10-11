# Complete Pipeline Acceptance Checklist

Use this checklist to decide whether a build is actually release-ready. A checked item must have reproducible evidence, not only documentation or a model-generated claim.

## Build and packaging
- [ ] Clean install on Windows, macOS, and Linux
- [ ] Reproducible dependency installation and dependency lock/SBOM
- [ ] Python compile, JavaScript syntax, lint, type checks, dependency scan
- [ ] CI green on supported Python versions

## Provider contract
- [ ] No-provider startup has a clear setup state
- [ ] Local Ollama model discovery and inference work
- [ ] Each cloud provider has fixture tests and opt-in live contract tests
- [ ] Streaming, timeouts, malformed responses, rate limits, and cancellation are tested
- [ ] Token/cost accounting covers helper and parallel calls, or clearly says incomplete
- [ ] No silent local-to-cloud fallback

## Workspace and tools
- [ ] Traversal, symlink escape, TOCTOU, oversized files, and malicious tool paths tested
- [ ] Locking works across processes or deployment enforces one server per workspace
- [ ] Every file mutation produces a reviewable diff and recoverable backup
- [ ] Destructive tools require explicit approval and leave an audit event
- [ ] Tool/MCP outputs are treated as untrusted data

## Crew/LOGOS pipeline
- [ ] Architect output includes task plan, dependencies, acceptance criteria, and path ownership
- [ ] Parallel tasks cannot write overlapping paths without coordination
- [ ] Builder output is staged and diffed before commit
- [ ] Reviewer runs deterministic checks on the candidate code
- [ ] Failed tests cannot be overridden by model consensus
- [ ] Repair attempts are bounded and re-verified after changes
- [ ] Run status, mailbox, cancellation, restart recovery, and error telemetry are tested
- [ ] LOGOS modules are benchmarked against a simple baseline with ablations

## UI and usability
- [ ] Browser E2E against a real local server, not only stubbed endpoints
- [ ] Chat, settings, file browser/editor, projects, artifacts, MCP, and crew panels tested
- [ ] Keyboard-only navigation, screen reader labels, contrast, reduced motion, mobile widths
- [ ] Errors and loading states are visible; no fabricated metrics or fake provider status
- [ ] Run timeline includes timestamps, events, file diffs, tests, provider/model, and provenance

## Security and release
- [ ] OS-enforced sandbox/container/VM for untrusted execution
- [ ] Resource limits for CPU, RAM, processes, wall time, output, network, and filesystem
- [ ] Authentication/CSRF/origin controls before any non-loopback exposure
- [ ] Secrets never appear in logs, traces, prompts, or exported artifacts
- [ ] Threat model and independent security review completed
