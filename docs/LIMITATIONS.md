# Limitations and known gaps (production-ready pass)

- **Not an OS sandbox.** The subprocess runner uses timeouts and temp dirs only. Untrusted code
  needs the container path (`execute_untrusted_container`, requires Docker/Podman) or a VM.
- **Single-user, loopback only.** No authentication/CSRF protection; do not bind to a public interface.
- **No in-flight cancellation** of synchronous provider requests; stop/pause act at checkpoints.
- **Model quality is not benchmarked here.** `agentufo-bench` measures the local control plane and
  the workspace policy only. `agent_core/routing_benchmark.py` is a harness for model-routing
  evaluations, but no provider results are published because none were run.
- **Typing debt.** 15 legacy modules (listed in `pyproject.toml` `[tool.mypy.overrides]`) still
  have ~75 mypy findings (mostly Optional narrowing). They are excluded from the CI gate; the
  rest of the code type-checks cleanly.
- **Lint debt.** E402/E741/B904/B905/B007/F841 are ignored for legacy code.
- **Docker image** was not built in the authoring environment (no Docker daemon); CI does not build it.
- The *policy corpus* in `agent_core/bench.py` has 14 attack and 6 benign cases written by the
  authors. 14/14 blocked is a regression check, not a proof of security.
