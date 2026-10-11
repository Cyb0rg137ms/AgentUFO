# Security posture and known limits

AgentUFO can edit real project files and execute generated code. Treat model output and project
contents as untrusted input.

## Hardening included in this build

- The HTTP server binds to `127.0.0.1` by default.
- Static file serving resolves and confines requested assets to the `web/` root.
- Workspace file reads and writes resolve symlinks and reject paths outside the active project root.
- Workspace lock paths reject traversal rather than remapping outside paths to a same-named file.
- JSON request bodies are size-limited and malformed/non-object JSON is rejected with HTTP 400.
- The API does not emit permissive wildcard CORS headers.
- Artifact previews run in sandboxed iframes without combining `allow-scripts` with `allow-same-origin`.
- JSON/static responses include basic no-sniff, referrer, cache, and permissions headers.
- Generation output budgets are bounded; the Ollama provider no longer uses an unlimited `num_predict=-1` sentinel for ordinary calls.
- A missing model provider is represented as an explicit unavailable state. Generation fails with an actionable setup error, while the UI/server remains available for setup.

## Important limitations — not a security certification

Single-file Python/Node execution defaults to a Docker/Podman container with networking disabled,
read-only root and source mounts, dropped Linux capabilities, no-new-privileges, and CPU/memory/PID
limits. The required image must already exist locally; secure mode fails closed if the container runtime
is unavailable. The explicit `mode="host"` / `AGENTUFO_SANDBOX_MODE=host` compatibility mode still uses
host subprocesses and **is not an OS security boundary**. Secure multi-file project execution is not yet
implemented and fails closed. Generated code should not run with privileged host credentials.

Before a public/multi-user deployment, add authenticated access, CSRF/origin policy, per-user workspace
isolation, secure multi-file execution, end-to-end enforcement for every MCP adapter, secret isolation,
resource quotas, structured audit retention, dependency pinning/SBOM, and a formal threat model. Validate
the container path with adversarial tests on each supported OS. The default loopback binding is intentional.
- Workspace synchronization is process-local. The persisted lock registry is not yet protected by a cross-process OS lock, so two independent server processes must not share a workspace concurrently.


## Sandboxed project execution and MCP policy

Container mode is the default and fails closed if Docker/Podman or a pre-pulled runtime image is unavailable. Multi-file Python/Node project bundles are bounded and mounted read-only with networking disabled, no Linux capabilities, a non-root UID, a read-only container root, and CPU/memory/PID limits. This is defense-in-depth; run the optional real-container integration tests on the target host before trusting the deployment. `mode="host"` is for local development/tests only.

Outbound MCP integrations must route tool calls through `AuthorizedMCPClient` with fully qualified `server:tool` allowlist entries. Do not expose the raw MCP client to model-generated code. A trusted user-interface approval flow must control any approval grant; do not let an LLM set approval flags itself.
