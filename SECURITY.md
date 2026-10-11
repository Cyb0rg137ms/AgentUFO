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

The current execution sandbox uses temporary directories and subprocess timeouts, but **a subprocess is
not an OS security boundary**. Generated code may still have access to the host user’s permissions and
network unless the operating system or an external sandbox restricts it. Do not expose this server to
untrusted networks or run untrusted tasks with privileged credentials.

Before a public/multi-user deployment, add authenticated access, CSRF/origin policy, per-user workspace
isolation, a container/VM or OS sandbox with network and filesystem restrictions, secret isolation,
resource quotas, structured audit logs, dependency pinning/SBOM, and a formal threat model. The default
loopback binding is intentional.
- Workspace synchronization is process-local. The persisted lock registry is not yet protected by a cross-process OS lock, so two independent server processes must not share a workspace concurrently.
