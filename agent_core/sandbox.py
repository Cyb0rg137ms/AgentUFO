"""
sandbox.py
==========
High-performance multi-language execution sandbox for running scripts, test suites,
and multi-file projects across isolated environments.

Supported Runtimes:
  - Python (sys.executable)
  - JavaScript / Node.js (node)
  - TypeScript (npx tsx / ts-node / node)
  - Shell / PowerShell / Bash (.ps1, .sh, .bat, .cmd)
  - HTML5 / DOM structure checks (not browser execution)
  - Multi-File Project Execution Engine
"""

from __future__ import annotations

import os
import re
import json
import sys
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class CodeExecutionSandbox:
    """
    Manages the creation, execution, and validation of code across multiple runtimes
    in sandboxed temporary directories with resource timeouts and security guards.
    """

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self.timeout = timeout_seconds

    def detect_runtime(self, filename: str, code_content: str) -> str:
        """Infers the runtime from filename extension or code markers."""
        fn_low = filename.lower()
        if fn_low.endswith(".py"):
            return "python"
        elif fn_low.endswith(".js") or fn_low.endswith(".mjs"):
            return "node"
        elif fn_low.endswith(".ts") or fn_low.endswith(".tsx"):
            return "typescript"
        elif fn_low.endswith(".html") or fn_low.endswith(".htm"):
            return "html"
        elif fn_low.endswith(".ps1"):
            return "powershell"
        elif fn_low.endswith(".sh") or fn_low.endswith(".bash"):
            return "bash"
        elif fn_low.endswith(".bat") or fn_low.endswith(".cmd"):
            return "batch"

        # Heuristic detection from content
        clean = code_content.strip()
        clean_low = clean.lower()
        if clean_low.startswith("<!doctype html") or "<html" in clean_low or "<canvas" in clean_low:
            return "html"
        if "console.log" in clean or "require(" in clean or "export default" in clean or "import " in clean and "from '" in clean:
            if "interface " in clean or ": string" in clean or ": number" in clean:
                return "typescript"
            return "node"
        if "def " in clean or "import " in clean or "print(" in clean or "class " in clean:
            return "python"
        if clean.startswith("#!/bin/bash") or clean.startswith("#!/bin/sh"):
            return "bash"

        return "python"

    def execute_script(
        self,
        code_content: str,
        filename: str = "temp_script.py",
        runtime: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Executes a script in a temporary isolated sandbox.

        Args:
            code_content: Code to execute.
            filename: Target file name inside the sandbox.
            runtime: Optional explicit runtime ('python', 'node', 'typescript', 'powershell', 'bash', 'html').

        Returns:
            (success: bool, logs: str)
        """
        target_runtime = runtime or self.detect_runtime(filename, code_content)
        suffix = Path(filename).suffix.lower()

        # Static validators for non-executable project artifacts. These are explicitly
        # labelled static checks; they do not claim browser or application-level execution.
        if suffix == ".json":
            try:
                json.loads(code_content)
                return True, "JSON parse passed (static validation; no application tests executed)."
            except (ValueError, TypeError) as exc:
                return False, f"JSON parse failed: {exc}"
        if suffix == ".md":
            if not code_content.strip():
                return False, "Markdown validation failed: file is empty."
            fence_count = len(re.findall(r"(?m)^\s*(```|~~~)", code_content))
            if fence_count % 2:
                return False, "Markdown validation failed: fenced code block delimiters are unbalanced."
            return True, "Markdown structure checks passed (static validation; no rendered preview executed)."
        if suffix == ".css":
            cleaned = re.sub(r"/\*[\s\S]*?\*/", "", code_content)
            cleaned = re.sub(r"(['\"]).*?\1", "", cleaned)
            if cleaned.count("{") != cleaned.count("}") or cleaned.count("{") == 0 and cleaned.strip():
                return False, "CSS structure check failed: unbalanced or missing rule braces."
            return True, "CSS brace structure passed (static validation; no browser render executed)."
        if suffix == ".toml":
            try:
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib  # type: ignore[no-redef]
                tomllib.loads(code_content)
                return True, "TOML parse passed (static validation; no application tests executed)."
            except ImportError:
                return False, "TOML validation requires Python 3.11+ or the optional tomli package."
            except Exception as exc:
                return False, f"TOML parse failed: {exc}"

        # HTML Validation Path
        if target_runtime == "html":
            return self.validate_html(code_content)

        # Security boundary guard against dangerous commands
        blocklist = [
            "import os; os.system", "subprocess.call", "shutil.rmtree", "eval(",
            "format c:", "del /f /s /q c:", "rm -rf /",
        ]
        code_lower = code_content.lower()
        if any(bad in code_lower for bad in blocklist):
            return False, "Security Violation: Blocked malicious system operations."

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / filename
            try:
                file_path.write_text(code_content, encoding="utf-8")
            except Exception as e:
                return False, f"File IO error in sandbox initialization: {e}"

            cmd: List[str] = []
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"

            if target_runtime == "python":
                cmd = [sys.executable, "-u", str(file_path)]

            elif target_runtime in ("node", "javascript"):
                node_bin = shutil.which("node")
                if not node_bin:
                    return False, "Node.js executable not found in PATH."
                cmd = [node_bin, str(file_path)]

            elif target_runtime == "typescript":
                # Check for tsx or ts-node, fallback to node
                tsx_bin = shutil.which("tsx") or shutil.which("ts-node")
                if tsx_bin:
                    cmd = [tsx_bin, str(file_path)]
                elif shutil.which("npx"):
                    cmd = ["npx", "-y", "tsx", str(file_path)]
                else:
                    node_bin = shutil.which("node")
                    if node_bin:
                        cmd = [node_bin, str(file_path)]
                    else:
                        return False, "TypeScript runtime (tsx/node) not found in PATH."

            elif target_runtime == "powershell":
                ps_bin = shutil.which("powershell") or shutil.which("pwsh") or "powershell.exe"
                cmd = [ps_bin, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(file_path)]

            elif target_runtime == "bash":
                bash_bin = shutil.which("bash") or shutil.which("sh")
                if bash_bin:
                    cmd = [bash_bin, str(file_path)]
                else:
                    # Windows fallback: execute with powershell
                    ps_bin = shutil.which("powershell") or "powershell.exe"
                    cmd = [ps_bin, "-NoProfile", "-Command", f"Get-Content '{file_path}' | & {{ $input }}"]

            elif target_runtime == "batch":
                cmd = ["cmd.exe", "/c", str(file_path)]

            else:
                cmd = [sys.executable, "-u", str(file_path)]

            try:
                result = subprocess.run(
                    cmd,
                    cwd=tmpdir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout,
                    text=True,
                    env=env,
                )
                logs = result.stdout + result.stderr

                # Check for failure indicators
                has_error = (
                    result.returncode != 0
                    or "Traceback (most recent call last):" in logs
                    or "AssertionError" in logs
                    or "FAILED (" in logs
                    or "ERROR: " in logs
                    or "SyntaxError:" in logs
                    or "NameError:" in logs
                    or "TypeError:" in logs
                    or "ZeroDivisionError:" in logs
                    or "ReferenceError:" in logs
                    or "Uncaught " in logs
                )
                success = (result.returncode == 0) and not has_error
                return success, logs

            except subprocess.TimeoutExpired:
                return False, f"Execution Error: Process timed out (exceeded {self.timeout}s limit)."
            except Exception as e:
                return False, f"Subprocess initialization error: {e}"

    def execute_project(
        self,
        files: Dict[str, str],
        entrypoint: str,
        runtime: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Executes a multi-file project inside an isolated sandbox directory.

        Args:
            files: Dictionary mapping relative file paths to file contents.
            entrypoint: Path to the entrypoint file to run.
            runtime: Optional runtime override.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            for rel_path, content in files.items():
                target = base / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            entry_path = base / entrypoint
            if not entry_path.exists():
                return False, f"Project entrypoint '{entrypoint}' not found in project files."

            entry_content = entry_path.read_text(encoding="utf-8")
            target_runtime = runtime or self.detect_runtime(entrypoint, entry_content)

            cmd: List[str] = []
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"

            if target_runtime == "python":
                cmd = [sys.executable, "-u", str(entry_path)]
            elif target_runtime in ("node", "javascript"):
                node_bin = shutil.which("node") or "node"
                cmd = [node_bin, str(entry_path)]
            elif target_runtime == "typescript":
                tsx_bin = shutil.which("tsx") or shutil.which("ts-node")
                cmd = [tsx_bin or "node", str(entry_path)]
            elif target_runtime == "powershell":
                cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(entry_path)]
            else:
                cmd = [sys.executable, "-u", str(entry_path)]

            try:
                result = subprocess.run(
                    cmd,
                    cwd=tmpdir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout,
                    text=True,
                    env=env,
                )
                logs = result.stdout + result.stderr
                success = (result.returncode == 0) and ("Traceback" not in logs and "Error:" not in logs)
                return success, logs
            except subprocess.TimeoutExpired:
                return False, f"Project execution timed out after {self.timeout}s."
            except Exception as e:
                return False, f"Project execution error: {e}"

    def validate_html(self, html_content: str) -> Tuple[bool, str]:
        """Validates HTML structure, canvas elements, and embedded script tags."""
        clean = html_content.strip()
        clean_low = clean.lower()
        logs: List[str] = []

        if not ("<html" in clean_low or "<!doctype" in clean_low or "<div" in clean_low or "<canvas" in clean_low):
            return False, "HTML Validation Failed: No HTML or DOM root tags found."

        logs.append("HTML/DOM root markers detected; structural completeness is not yet proven.")
        if "<canvas" in clean_low:
            logs.append("Canvas 2D render surface detected.")
        if "<script" in clean_low and "</script>" in clean_low:
            logs.append("Embedded JavaScript script block detected; syntax/runtime status is checked separately when tooling is available.")
            # Check script blocks for obvious syntax errors via node or py ast if python
            scripts = re.findall(r'<script[^>]*>(.*?)</script>', html_content, re.DOTALL | re.IGNORECASE)
            node_bin = shutil.which("node")
            if node_bin:
                for idx, sc in enumerate(scripts, start=1):
                    if sc.strip():
                        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                            f.write(sc)
                            temp_js = f.name
                        try:
                            res = subprocess.run([node_bin, "--check", temp_js], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0)
                            if res.returncode != 0:
                                return False, f"HTML <script> Block #{idx} Syntax Error:\n{res.stderr}"
                            logs.append(f"Script block #{idx} passed JavaScript syntax check.")
                        except Exception:
                            pass
                        finally:
                            try:
                                os.unlink(temp_js)
                            except Exception:
                                pass
        elif "<script" in clean_low and "</script>" not in clean_low:
            return False, "HTML Validation Error: Unclosed <script> tag detected."

        return True, "\n".join(logs)
