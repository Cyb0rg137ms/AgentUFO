"""Offline, provider-free benchmarks for AgentUFO.

Two things are measured, both reproducible on a CPU-only machine with no model
provider configured:

1. **HTTP control-plane latency** of the local server (``/healthz``, the UI shell,
   run ledger, model list, status, file tree) using a real ``ThreadingHTTPServer``
   bound to an ephemeral loopback port. Each request opens a new TCP connection
   (``urllib``), so numbers include connection setup.
2. **Workspace policy enforcement**: a fixed corpus of adversarial and benign
   filesystem requests is run through :class:`agent_core.permissions.WorkspacePolicy`
   and the block / allow decisions are compared with the expected outcome.

Nothing here calls an LLM; model quality is out of scope for this benchmark.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_core.permissions import PermissionDenied, WorkspacePolicy

log = logging.getLogger("agentufo.bench")

DEFAULT_ENDPOINTS: tuple[str, ...] = (
    "/healthz",
    "/",
    "/api/crew/runs",
    "/api/models",
    "/api/status",
    "/api/fs/tree",
)


@dataclass(frozen=True)
class PolicyCase:
    name: str
    path: str
    operation: str
    should_allow: bool


def policy_corpus(outside: str) -> list[PolicyCase]:
    """Fixed corpus. ``outside`` is an absolute path outside the workspace."""
    deny = [
        PolicyCase("parent traversal", "../secret.txt", "read", False),
        PolicyCase("nested traversal", "src/../../etc/passwd", "read", False),
        PolicyCase("absolute outside", outside, "read", False),
        PolicyCase("workspace root itself", ".", "write", False),
        PolicyCase(".env read", ".env", "read", False),
        PolicyCase("nested .env", "app/.env", "read", False),
        PolicyCase(".env variant", "app/.env.production", "read", False),
        PolicyCase(".git internals", "repo/.git/config", "write", False),
        PolicyCase("ssh key", "home/id_rsa", "read", False),
        PolicyCase("pem file", "certs/server.pem", "read", False),
        PolicyCase("key file", "certs/server.key", "read", False),
        PolicyCase("delete without approval", "src/main.py", "delete", False),
        PolicyCase("unknown operation", "src/main.py", "chmod", False),
        PolicyCase("symlink escape", "escape/link.txt", "read", False),
    ]
    allow = [
        PolicyCase("read source", "src/main.py", "read", True),
        PolicyCase("write source", "src/new_module.py", "write", True),
        PolicyCase("read nested", "a/b/c/d.txt", "read", True),
        PolicyCase("write docs", "docs/README.md", "write", True),
        PolicyCase("env-like name", "src/environment.py", "read", True),
        PolicyCase("read toml", "pyproject.toml", "read", True),
    ]
    return deny + allow


def run_policy_bench() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as other:
        root = Path(ws)
        secret = Path(other) / "secret.txt"
        secret.write_text("x")
        symlink_supported = True
        try:
            (root / "escape").mkdir()
            os.symlink(secret, root / "escape" / "link.txt")
        except OSError:
            symlink_supported = False
        policy = WorkspacePolicy(root)
        rows = []
        for case in policy_corpus(str(secret)):
            if case.name == "symlink escape" and not symlink_supported:
                continue
            t0 = time.perf_counter()
            try:
                policy.resolve(case.path, case.operation)
                allowed, reason = True, ""
            except PermissionDenied as exc:
                allowed, reason = False, str(exc)
            dt_us = (time.perf_counter() - t0) * 1e6
            rows.append(
                {
                    "name": case.name,
                    "path": case.path if case.name != "absolute outside" else "<tmp>/secret.txt",
                    "operation": case.operation,
                    "expected_allow": case.should_allow,
                    "allowed": allowed,
                    "correct": allowed == case.should_allow,
                    "reason": reason,
                    "latency_us": round(dt_us, 2),
                }
            )
    attacks = [r for r in rows if not r["expected_allow"]]
    benign = [r for r in rows if r["expected_allow"]]
    return {
        "cases": rows,
        "attacks": len(attacks),
        "attacks_blocked": sum(not r["allowed"] for r in attacks),
        "benign": len(benign),
        "benign_allowed": sum(r["allowed"] for r in benign),
        "all_correct": all(r["correct"] for r in rows),
    }


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[idx]


def run_http_bench(
    requests: int = 200,
    concurrency: int = 8,
    endpoints: tuple[str, ...] = DEFAULT_ENDPOINTS,
) -> dict[str, Any]:
    """Start the real handler on an ephemeral port inside a temp working dir."""
    from http.server import ThreadingHTTPServer

    from server.web_server import AgentIDERequestHandler

    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), AgentIDERequestHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def hit(path: str) -> float:
            t0 = time.perf_counter()
            with urllib.request.urlopen(base + path, timeout=10) as resp:
                resp.read()
                if resp.status != 200:
                    raise RuntimeError(f"{path} -> {resp.status}")
            return (time.perf_counter() - t0) * 1e3

        try:
            per_endpoint: dict[str, Any] = {}
            for ep in endpoints:
                for _ in range(5):
                    hit(ep)  # warm-up
                lat = [hit(ep) for _ in range(requests)]
                per_endpoint[ep] = {
                    "n": requests,
                    "p50_ms": round(statistics.median(lat), 3),
                    "p95_ms": round(_pct(lat, 0.95), 3),
                    "p99_ms": round(_pct(lat, 0.99), 3),
                    "mean_ms": round(statistics.fmean(lat), 3),
                }
                log.info("%s p50=%.2fms", ep, per_endpoint[ep]["p50_ms"])
            total = requests * 2
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                conc = list(pool.map(hit, ["/healthz"] * total))
            wall = time.perf_counter() - t0
            concurrent = {
                "endpoint": "/healthz",
                "concurrency": concurrency,
                "requests": total,
                "throughput_rps": round(total / wall, 1),
                "p50_ms": round(statistics.median(conc), 3),
                "p95_ms": round(_pct(conc, 0.95), 3),
            }
        finally:
            httpd.shutdown()
            httpd.server_close()
            os.chdir(cwd)
    return {"endpoints": per_endpoint, "concurrent": concurrent}


def run_all(requests: int = 200, concurrency: int = 8) -> dict[str, Any]:
    import platform

    return {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "provider": "none (offline benchmark)",
        },
        "http": run_http_bench(requests=requests, concurrency=concurrency),
        "policy": run_policy_bench(),
    }


def plot(results: dict[str, Any], out: str | Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    eps = results["http"]["endpoints"]
    names = list(eps)
    p50 = [eps[n]["p50_ms"] for n in names]
    p95 = [eps[n]["p95_ms"] for n in names]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.2), gridspec_kw={"width_ratios": [3, 2]})
    x = range(len(names))
    a1.bar([i - 0.2 for i in x], p50, 0.4, label="p50")
    a1.bar([i + 0.2 for i in x], p95, 0.4, label="p95")
    a1.set_xticks(list(x), names, rotation=25, ha="right")
    a1.set_ylabel("latency (ms, new TCP conn per request)")
    c = results["http"]["concurrent"]
    a1.set_title(
        f"Local control-plane latency (n={eps[names[0]]['n']}/endpoint); "
        f"/healthz x{c['concurrency']}: {c['throughput_rps']} req/s"
    )
    a1.legend()
    pol = results["policy"]
    cases = pol["cases"]
    colors = ["tab:green" if r["correct"] else "tab:red" for r in cases]
    a2.barh(range(len(cases)), [1] * len(cases), color=colors)
    a2.set_yticks(
        range(len(cases)),
        [f"{r['name']} ({'allow' if r['allowed'] else 'deny'})" for r in cases],
        fontsize=7,
    )
    a2.set_xticks([])
    a2.invert_yaxis()
    a2.set_title(
        f"WorkspacePolicy: {pol['attacks_blocked']}/{pol['attacks']} attacks blocked, "
        f"{pol['benign_allowed']}/{pol['benign']} benign allowed",
        fontsize=9,
    )
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="agentufo-bench", description=__doc__.splitlines()[0])
    p.add_argument("--requests", type=int, default=200)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--plot", default=None, help="PNG path (needs matplotlib)")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING)
    res = run_all(requests=a.requests, concurrency=a.concurrency)
    text = json.dumps(res, indent=2)
    if a.json_out:
        Path(a.json_out).write_text(text + "\n")
    else:
        print(text)
    if a.plot:
        plot(res, a.plot)
    return 0 if res["policy"]["all_correct"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
