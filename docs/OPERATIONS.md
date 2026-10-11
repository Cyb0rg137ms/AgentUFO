# Operations and Troubleshooting

## Local startup

Python 3.10+ is required. Use the commands in `README.md`. Keep the default bind address at `127.0.0.1` for local use.

## Provider setup

- **Ollama:** install/start Ollama separately and pull a model supported by your machine. The app does not bundle model weights.
- **Cloud providers:** copy `.env.example` to `.env`, add only the provider keys you plan to use, and keep `.env` out of version control.
- If no provider is configured, the app should report setup required. Do not interpret an app health response as proof that inference works.

## Validation pipeline

```powershell
python -m compileall -q agent_core server run_web_ui.py
python -m unittest discover -s tests -v
node --check web/js/app.js
```

Optional developer checks (install with `python -m pip install -e ".[dev,qdit]"`; use `python -m pip`, not `pip.exe`, on Windows systems where standalone launchers are blocked):

```powershell
ruff check agent_core server run_web_ui.py tests
bandit -r agent_core server
```

## Common problems

- **Port already in use:** start with `python run_web_ui.py --port 8001`.
- **No model:** start Ollama or configure a provider key, then restart/reconfigure in the UI.
- **Provider/model mismatch:** select a model name that actually exists for the provider; model catalog names can change over time.
- **Generated code fails tests:** keep the failure log, inspect the diff, fix the underlying issue, and rerun the relevant test. Do not mark success because a model says it succeeded.
- **Suspicious file path:** stop the run and inspect path normalization/symlink handling. Do not relax workspace boundaries to make the error disappear.

## Security boundary

The app is a local developer tool, not a hardened multi-tenant service. The subprocess runner is not a container or VM. Do not expose it to the public internet or run untrusted generated code on sensitive host environments. See `SECURITY.md` for release blockers.
