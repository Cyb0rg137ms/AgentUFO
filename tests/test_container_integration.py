"""Opt-in integration tests: they exercise real container isolation when available."""

import shutil
import unittest

from agent_core.sandbox import CodeExecutionSandbox

ENGINE = shutil.which("docker") or shutil.which("podman")


@unittest.skipUnless(ENGINE, "Docker/Podman runtime is not installed in this environment")
class ContainerIsolationIntegrationTests(unittest.TestCase):
    def test_untrusted_code_runs_unprivileged_with_readonly_source(self):
        code = r"""import os, socket
print("uid", os.geteuid() if hasattr(os, "geteuid") else "windows-container")
try:
    open("/input/main.py", "w").write("mutated")
    print("SOURCE_WRITE_ALLOWED")
except OSError:
    print("SOURCE_READONLY")
try:
    open("/etc/agentufo-write-probe", "w").write("bad")
    print("ROOT_WRITE_ALLOWED")
except OSError:
    print("ROOT_READONLY")
try:
    socket.create_connection(("1.1.1.1", 53), timeout=0.5)
    print("NETWORK_AVAILABLE")
except OSError:
    print("NETWORK_BLOCKED")
"""
        ok, output = CodeExecutionSandbox(timeout_seconds=10).execute_untrusted_container(
            code, "main.py", runtime="python"
        )
        if "not available locally" in output:
            self.skipTest(
                "python:3.12-alpine image is not pre-pulled; pull it before running container integration tests"
            )
        self.assertTrue(ok, output)
        self.assertIn("uid 65534", output)
        self.assertIn("SOURCE_READONLY", output)
        self.assertIn("ROOT_READONLY", output)
        self.assertIn("NETWORK_BLOCKED", output)
        self.assertNotIn("SOURCE_WRITE_ALLOWED", output)
        self.assertNotIn("ROOT_WRITE_ALLOWED", output)
        self.assertNotIn("NETWORK_AVAILABLE", output)

    def test_multifile_python_project_imports_under_same_isolation_profile(self):
        files = {
            "main.py": "from pkg.helper import value\nprint('multi-file', value())\n",
            "pkg/__init__.py": "",
            "pkg/helper.py": "def value(): return 42\n",
        }
        ok, output = CodeExecutionSandbox(timeout_seconds=10).execute_project(
            files, "main.py", runtime="python"
        )
        if "not available locally" in output:
            self.skipTest("python:3.12-alpine image is not pre-pulled")
        self.assertTrue(ok, output)
        self.assertIn("multi-file 42", output)
