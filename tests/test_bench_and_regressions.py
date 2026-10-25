"""Tests for the offline benchmark and for bugs fixed in the production-ready pass."""

from __future__ import annotations

import io
import json
from unittest import mock

from agent_core import bench
from agent_core.capability_prober import EmpiricalCapabilityProber
from agent_core.llm import cloud_providers


def test_policy_bench_blocks_every_attack_and_allows_benign():
    res = bench.run_policy_bench()
    assert res["all_correct"], [c for c in res["cases"] if not c["correct"]]
    assert res["attacks_blocked"] == res["attacks"] >= 13
    assert res["benign_allowed"] == res["benign"] == 6


def test_http_bench_measures_all_endpoints():
    res = bench.run_http_bench(requests=5, concurrency=2)
    assert set(res["endpoints"]) == set(bench.DEFAULT_ENDPOINTS)
    for stats in res["endpoints"].values():
        assert 0 < stats["p50_ms"] <= stats["p95_ms"] <= stats["p99_ms"]
    assert res["concurrent"]["requests"] == 10
    assert res["concurrent"]["throughput_rps"] > 0


def test_bench_cli_writes_json_and_plot(tmp_path):
    out, png = tmp_path / "b.json", tmp_path / "b.png"
    rc = bench.main(
        ["--requests", "3", "--concurrency", "2", "--json", str(out), "--plot", str(png)]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["environment"]["provider"].startswith("none")
    assert png.stat().st_size > 10_000


def test_auto_probe_daemon_no_longer_raises_nameerror(tmp_path):
    """Regression: capability_prober used time.time() without importing time."""
    prober = EmpiricalCapabilityProber(workspace_root=tmp_path)
    prober._profiles["m:1"] = mock.MagicMock(probed_at=0.0)  # stale -> time.time() path
    payload = io.BytesIO(json.dumps({"models": [{"name": "m:1"}]}).encode())
    fake = mock.MagicMock()
    fake.__enter__.return_value = payload
    with (
        mock.patch("urllib.request.urlopen", return_value=fake),
        mock.patch.object(prober, "probe_model") as probe,
    ):
        prober.auto_probe_daemon("http://127.0.0.1:1")
    probe.assert_called_once_with("m:1")


def test_openai_compatible_providers_are_single_definitions():
    """The duplicated (shadowed) provider classes were removed; the live ones remain."""
    for name, env in [
        ("DeepSeekProvider", "deepseek"),
        ("KimiProvider", "kimi"),
        ("MiniMaxProvider", "minimax"),
        ("QwenProvider", "qwen"),
    ]:
        cls = getattr(cloud_providers, name)
        p = cls(api_key="x")
        assert env in p.provider_name.lower()
