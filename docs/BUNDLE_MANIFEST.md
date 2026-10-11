# Bundle manifest

- `agent_core/`: coordinator, real LLM providers, workspace manager, tools, budget/capability controls, review gate, durable crew memory, and LOGOS experiments.
- `server/`: local HTTP server, API handlers, durable run control plane, and starter artifacts.
- `web/`: redesigned Control Room UI, model router, Ollama setup, live crew telemetry, workspace explorer/editor, and minimizable event tray.
- `tests/`: API hardening, workspace boundaries, budget guard, candidate-overlay review, crew run lifecycle, and frontend contract tests.
- `docs/`: architecture, operations, complete-pipeline checklist, bundle manifest, and release limitations.
- `.github/workflows/ci.yml`: Linux, Windows, and macOS CI matrix.
- `scripts/run-local.ps1`, `scripts/run-local.sh`, `START_AGENTUFO.bat`: local launch helpers; the Windows launcher avoids direct `pip.exe` execution.
- `run_web_ui.py`, `pyproject.toml`, `requirements.txt`, `.env.example`, `LICENSE`: entry point, packaging, optional extras, configuration template, and license.

No API credentials, model weights, Python virtual environments, caches, or user project workspace are included. The base UI/server starts without third-party dependencies; NumPy is optional for the Qdit tensor experiment.
