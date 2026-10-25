"""Example: enforce a workspace policy and run the offline benchmark programmatically."""

from __future__ import annotations

import tempfile

from agent_core.bench import run_http_bench
from agent_core.permissions import PermissionDenied, WorkspacePolicy

with tempfile.TemporaryDirectory() as ws:
    policy = WorkspacePolicy(ws)
    for path, op in [("src/app.py", "write"), ("../etc/passwd", "read"), (".env", "read")]:
        try:
            policy.resolve(path, op)
            print(f"ALLOW {op:5s} {path}")
        except PermissionDenied as exc:
            print(f"DENY  {op:5s} {path}: {exc}")

stats = run_http_bench(requests=20, concurrency=4)
for ep, s in stats["endpoints"].items():
    print(f"{ep:16s} p50={s['p50_ms']:.2f} ms  p95={s['p95_ms']:.2f} ms")
