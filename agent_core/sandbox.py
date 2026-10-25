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

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class CodeExecutionSandbox:
    """
    Provides legacy host-subprocess validation plus an explicit container-isolated API.

    The default mode is container isolation. ``mode="host"`` or
    ``AGENTUFO_SANDBOX_MODE=host`` is an explicit development-only opt-in and is NOT
    a security boundary. Container mode fails closed when Docker/Podman or the image
    is absent; secure multi-file project execution is not implemented yet.
    """

    def __init__(self, timeout_seconds: float = 5.0, mode: Optional[str] = None) -> None:
        self.timeout = timeout_seconds
        self.mode = (mode or os.environ.get("AGENTUFO_SANDBOX_MODE", "container")).strip().lower()
        if self.mode not in {"container", "host"}:
            raise ValueError("AGENTUFO_SANDBOX_MODE must be 'container' or 'host'")

    def execute_untrusted_container(
        self,
        code_content: str,
        filename: str = "main.py",
        *,
        runtime: str = "python",
        image: Optional[str] = None,
        memory_limit: str = "256m",
        cpu_limit: str = "1.0",
        pids_limit: int = 64,
        output_limit_bytes: int = 64_000,
    ) -> Tuple[bool, str]:
        """Execute untrusted code inside a locked-down Docker/Podman container.

        No in-process or host-process fallback is allowed. The container has no network,
        a read-only root filesystem, no Linux capabilities, no-new-privileges, bounded
        memory/CPU/PIDs, and only a read-only mount containing the submitted source.
        A container runtime and a pre-pulled image are prerequisites. This is defense in
        depth, not a substitute for patching the host/container runtime.
        """
        import re

        if runtime not in {"python", "node"}:
            return False, "Secure container mode currently supports only python and node runtimes."
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", Path(filename).name):
            return False, "Invalid source filename."
        if not re.fullmatch(r"[0-9]+(?:[kmg])", memory_limit.lower()):
            return False, "Invalid memory limit."
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", str(cpu_limit)):
            return False, "Invalid CPU limit."
        if not 1 <= pids_limit <= 512 or not 1024 <= output_limit_bytes <= 1_000_000:
            return False, "Resource limit outside permitted bounds."
        engine = shutil.which("docker") or shutil.which("podman")
        if not engine:
            return (
                False,
                "Secure execution unavailable: install Docker or Podman; unsafe host fallback is disabled.",
            )
        selected_image = image or (
            "python:3.12-alpine" if runtime == "python" else "node:22-alpine"
        )
        if not re.fullmatch(r"[A-Za-z0-9./:_-]{1,200}", selected_image):
            return False, "Invalid container image reference."
        with tempfile.TemporaryDirectory(prefix="agentufo-secure-") as tmpdir:
            source_dir = Path(tmpdir).resolve()
            source_path = source_dir / Path(filename).name
            try:
                source_path.write_text(code_content, encoding="utf-8")
                if os.name != "nt":
                    # The container runs as an unprivileged UID; make only the staged
                    # source directory traversable and source file readable.
                    os.chmod(source_dir, 0o755)
                    os.chmod(source_path, 0o644)
                if os.name == "nt":
                    # Docker Desktop accepts absolute Windows bind paths; convert slashes.
                    mount_path = str(source_dir).replace("\\", "/")
                else:
                    mount_path = str(source_dir)
                in_container = f"/input/{source_path.name}"
                command = [
                    engine,
                    "run",
                    "--rm",
                    "--pull=never",
                    "--network=none",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges:true",
                    "--pids-limit",
                    str(pids_limit),
                    "--memory",
                    memory_limit,
                    "--cpus",
                    str(cpu_limit),
                    "--user",
                    "65534:65534",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=16m",
                    "--mount",
                    f"type=bind,src={mount_path},dst=/input,readonly",
                    "--workdir",
                    "/tmp",
                    selected_image,
                ]
                command.extend(
                    ["python", "-I", in_container]
                    if runtime == "python"
                    else ["node", in_container]
                )
                container_name = "agentufo-" + uuid.uuid4().hex[:16]
                command[2:2] = ["--name", container_name]
                captured = {"stdout": bytearray(), "stderr": bytearray()}
                truncated = {"stdout": False, "stderr": False}

                def drain(stream, key):
                    # Always drain pipes to prevent child deadlock, but retain bounded logs.
                    cap = max(512, output_limit_bytes // 2)
                    while True:
                        chunk = stream.read(4096)
                        if not chunk:
                            break
                        room = cap - len(captured[key])
                        if room > 0:
                            captured[key].extend(chunk[:room])
                        if len(chunk) > room:
                            truncated[key] = True

                process = None
                try:
                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=os.environ.copy(),
                    )
                    readers = [
                        threading.Thread(
                            target=drain, args=(process.stdout, "stdout"), daemon=True
                        ),
                        threading.Thread(
                            target=drain, args=(process.stderr, "stderr"), daemon=True
                        ),
                    ]
                    for reader in readers:
                        reader.start()
                    try:
                        return_code = process.wait(timeout=self.timeout)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                        raise
                    for reader in readers:
                        reader.join(timeout=2)
                    stdout = bytes(captured["stdout"])
                    stderr = bytes(captured["stderr"])
                    combined = stdout + (b"\n" if stdout and stderr else b"") + stderr
                    output = combined.decode("utf-8", errors="replace")
                    if return_code == 125 and (
                        b"No such image" in combined or b"pull access" in combined
                    ):
                        return (
                            False,
                            f"Secure execution image {selected_image!r} is not available locally; pre-pull it explicitly.",
                        )
                    was_truncated = any(truncated.values())
                    return return_code == 0, output + (
                        "\n[output truncated]" if was_truncated else ""
                    )
                except subprocess.TimeoutExpired:
                    return False, f"Secure container execution timed out after {self.timeout}s."
                except OSError as exc:
                    return (
                        False,
                        f"Could not start secure container runtime: {type(exc).__name__}: {exc}",
                    )
                finally:
                    # A timed-out client must not leave an unobserved workload running.
                    try:
                        subprocess.run(
                            [engine, "rm", "-f", container_name],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=3,
                            check=False,
                            env=os.environ.copy(),
                        )
                    except Exception:
                        pass
            except OSError as exc:
                return (
                    False,
                    f"Could not stage source for secure execution: {type(exc).__name__}: {exc}",
                )

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
        if (
            "console.log" in clean
            or "require(" in clean
            or "export default" in clean
            or "import " in clean
            and "from '" in clean
        ):
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
        Execute a script according to the configured mode (container by default; host only by opt-in).

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
                return (
                    False,
                    "Markdown validation failed: fenced code block delimiters are unbalanced.",
                )
            return (
                True,
                "Markdown structure checks passed (static validation; no rendered preview executed).",
            )
        if suffix == ".css":
            cleaned = re.sub(r"/\*[\s\S]*?\*/", "", code_content)
            cleaned = re.sub(r"(['\"]).*?\1", "", cleaned)
            if (
                cleaned.count("{") != cleaned.count("}")
                or cleaned.count("{") == 0
                and cleaned.strip()
            ):
                return False, "CSS structure check failed: unbalanced or missing rule braces."
            return (
                True,
                "CSS brace structure passed (static validation; no browser render executed).",
            )
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
            "import os; os.system",
            "subprocess.call",
            "shutil.rmtree",
            "eval(",
            "format c:",
            "del /f /s /q c:",
            "rm -rf /",
        ]
        code_lower = code_content.lower()
        if any(bad in code_lower for bad in blocklist):
            return False, "Security Violation: Blocked malicious system operations."

        if self.mode == "container":
            secure_runtime = target_runtime
            if secure_runtime in ("javascript", "node"):
                secure_runtime = "node"
            if secure_runtime not in {"python", "node"}:
                return False, (
                    f"Secure mode does not support runtime {secure_runtime!r} yet. "
                    "Use Python/Node in container mode; host execution requires explicit mode='host'."
                )
            return self.execute_untrusted_container(
                code_content, filename=filename, runtime=secure_runtime
            )

        # Explicit development-only host execution. This is not a security boundary.
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
                    cmd = [
                        ps_bin,
                        "-NoProfile",
                        "-Command",
                        f"Get-Content '{file_path}' | & {{ $input }}",
                    ]

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
                return (
                    False,
                    f"Execution Error: Process timed out (exceeded {self.timeout}s limit).",
                )
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
        # Validate the bundle before either container or explicit development-host
        # execution. The host opt-in is not a security boundary, but it must not allow
        # archive/path traversal to write outside its temporary project directory.
        if not isinstance(files, dict) or not files:
            return False, "Project must contain at least one file."
        if len(files) > 250:
            return False, "Project contains too many files (limit 250)."
        normalized_files: Dict[str, str] = {}
        total_size = 0
        for raw_name, content in files.items():
            if not isinstance(raw_name, str) or not isinstance(content, str):
                return False, "Project file paths and contents must be strings."
            name = raw_name.replace("\\", "/")
            rel = Path(name)
            if (
                not name
                or name.startswith("/")
                or ".." in rel.parts
                or any(part in {"", "."} for part in name.split("/"))
                or re.match(r"^[A-Za-z]:", name)
                or "\x00" in name
            ):
                return False, f"Unsafe project path rejected: {raw_name!r}."
            total_size += len(content.encode("utf-8"))
            if total_size > 5_000_000:
                return False, "Project exceeds total size limit (5000000 bytes)."
            normalized_files[name] = content
        entrypoint = entrypoint.replace("\\", "/") if isinstance(entrypoint, str) else ""
        if entrypoint not in normalized_files:
            return False, f"Project entrypoint {entrypoint!r} not found in project files."
        files = normalized_files
        if self.mode == "container":
            return self._execute_project_container(files, entrypoint, runtime)
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
                cmd = [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(entry_path),
                ]
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
                success = (result.returncode == 0) and (
                    "Traceback" not in logs and "Error:" not in logs
                )
                return success, logs
            except subprocess.TimeoutExpired:
                return False, f"Project execution timed out after {self.timeout}s."
            except Exception as e:
                return False, f"Project execution error: {e}"

    def _execute_project_container(
        self,
        files: Dict[str, str],
        entrypoint: str,
        runtime: Optional[str] = None,
        *,
        memory_limit: str = "512m",
        cpu_limit: str = "1.0",
        pids_limit: int = 96,
        output_limit_bytes: int = 96_000,
        max_files: int = 250,
        max_total_bytes: int = 5_000_000,
    ) -> Tuple[bool, str]:
        """Run a bounded multi-file Python/Node project in a read-only container.

        The submitted project is staged into a fresh temporary directory and mounted
        read-only. No dependency installation or network access occurs. A missing
        runtime/image always fails closed; this method never falls back to host code.
        """
        if not isinstance(files, dict) or not files:
            return False, "Project must contain at least one file."
        if len(files) > max_files:
            return False, f"Project contains too many files (limit {max_files})."
        normalized: Dict[str, str] = {}
        total_bytes = 0
        for raw_name, content in files.items():
            if not isinstance(raw_name, str) or not isinstance(content, str):
                return False, "Project file paths and contents must be strings."
            name = raw_name.replace("\\", "/")
            rel = Path(name)
            if (
                not name
                or name.startswith("/")
                or ".." in rel.parts
                or any(part in {"", "."} for part in name.split("/"))
                or re.match(r"^[A-Za-z]:", name)
                or "\x00" in name
            ):
                return False, f"Unsafe project path rejected: {raw_name!r}."
            if len(content.encode("utf-8")) > max_total_bytes:
                return False, "A project file exceeds the maximum project size."
            total_bytes += len(content.encode("utf-8"))
            if total_bytes > max_total_bytes:
                return False, f"Project exceeds total size limit ({max_total_bytes} bytes)."
            normalized[name] = content
        entrypoint = entrypoint.replace("\\", "/")
        if entrypoint not in normalized:
            return False, f"Project entrypoint {entrypoint!r} not found in project files."
        target_runtime = runtime or self.detect_runtime(entrypoint, normalized[entrypoint])
        if target_runtime in {"javascript", "node"}:
            target_runtime = "node"
        if target_runtime not in {"python", "node"}:
            return (
                False,
                f"Secure multi-file mode supports Python and Node.js only, not {target_runtime!r}.",
            )
        if not re.fullmatch(r"[0-9]+(?:[kmg])", memory_limit.lower()):
            return False, "Invalid memory limit."
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", str(cpu_limit)):
            return False, "Invalid CPU limit."
        if not 1 <= pids_limit <= 512 or not 1024 <= output_limit_bytes <= 1_000_000:
            return False, "Resource limit outside permitted bounds."
        engine = shutil.which("docker") or shutil.which("podman")
        if not engine:
            return (
                False,
                "Secure project execution unavailable: install Docker or Podman; unsafe host fallback is disabled.",
            )
        selected_image = "python:3.12-alpine" if target_runtime == "python" else "node:22-alpine"
        with tempfile.TemporaryDirectory(prefix="agentufo-project-") as tmpdir:
            base = Path(tmpdir).resolve()
            try:
                for rel_name, content in normalized.items():
                    target = base.joinpath(*Path(rel_name).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # The input map contains only regular strings; all path components
                    # were normalized and traversal was rejected above.
                    target.write_text(content, encoding="utf-8")
                    if os.name != "nt":
                        os.chmod(target, 0o444)
                if os.name != "nt":
                    for directory in sorted(
                        (p for p in base.rglob("*") if p.is_dir()),
                        key=lambda p: len(p.parts),
                        reverse=True,
                    ):
                        os.chmod(directory, 0o555)
                    os.chmod(base, 0o555)
                mount_path = str(base).replace("\\", "/") if os.name == "nt" else str(base)
                command = [
                    engine,
                    "run",
                    "--rm",
                    "--pull=never",
                    "--network=none",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges:true",
                    "--pids-limit",
                    str(pids_limit),
                    "--memory",
                    memory_limit,
                    "--cpus",
                    str(cpu_limit),
                    "--user",
                    "65534:65534",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=32m",
                    "--mount",
                    f"type=bind,src={mount_path},dst=/workspace,readonly",
                    "--workdir",
                    "/workspace",
                    "--env",
                    "PYTHONDONTWRITEBYTECODE=1",
                    selected_image,
                ]
                entry_in_container = "/workspace/" + entrypoint
                command.extend(
                    ["python", "-B", entry_in_container]
                    if target_runtime == "python"
                    else ["node", entry_in_container]
                )
                container_name = "agentufo-project-" + uuid.uuid4().hex[:16]
                command[2:2] = ["--name", container_name]
                captured = {"stdout": bytearray(), "stderr": bytearray()}
                truncated = {"stdout": False, "stderr": False}
                cap = max(512, output_limit_bytes // 2)

                def drain(stream, key):
                    while True:
                        chunk = stream.read(4096)
                        if not chunk:
                            break
                        room = cap - len(captured[key])
                        if room > 0:
                            captured[key].extend(chunk[:room])
                        if len(chunk) > max(0, room):
                            truncated[key] = True

                process = None
                try:
                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=os.environ.copy(),
                    )
                    readers = [
                        threading.Thread(
                            target=drain, args=(process.stdout, "stdout"), daemon=True
                        ),
                        threading.Thread(
                            target=drain, args=(process.stderr, "stderr"), daemon=True
                        ),
                    ]
                    for reader in readers:
                        reader.start()
                    try:
                        return_code = process.wait(timeout=self.timeout)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                        raise
                    for reader in readers:
                        reader.join(timeout=2)
                    stdout, stderr = bytes(captured["stdout"]), bytes(captured["stderr"])
                    combined = stdout + (b"\n" if stdout and stderr else b"") + stderr
                    output = combined.decode("utf-8", errors="replace")
                    if return_code == 125 and (
                        b"No such image" in combined or b"pull access" in combined
                    ):
                        return (
                            False,
                            f"Secure execution image {selected_image!r} is not available locally; pre-pull it explicitly.",
                        )
                    if any(truncated.values()):
                        output += "\n[output truncated]"
                    return return_code == 0, output
                except subprocess.TimeoutExpired:
                    return False, f"Secure project execution timed out after {self.timeout}s."
                except OSError as exc:
                    return (
                        False,
                        f"Could not start secure container runtime: {type(exc).__name__}: {exc}",
                    )
                finally:
                    try:
                        subprocess.run(
                            [engine, "rm", "-f", container_name],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=3,
                            check=False,
                            env=os.environ.copy(),
                        )
                    except Exception:
                        pass
            except OSError as exc:
                return (
                    False,
                    f"Could not stage project for secure execution: {type(exc).__name__}: {exc}",
                )
            finally:
                # Restore staging permissions so TemporaryDirectory cleanup works on POSIX.
                if os.name != "nt":
                    try:
                        for directory in sorted(
                            (p for p in base.rglob("*") if p.is_dir()),
                            key=lambda p: len(p.parts),
                            reverse=True,
                        ):
                            os.chmod(directory, 0o755)
                        os.chmod(base, 0o755)
                    except OSError:
                        pass

    def validate_html(self, html_content: str) -> Tuple[bool, str]:
        """Validates HTML structure, canvas elements, and embedded script tags."""
        clean = html_content.strip()
        clean_low = clean.lower()
        logs: List[str] = []

        if not (
            "<html" in clean_low
            or "<!doctype" in clean_low
            or "<div" in clean_low
            or "<canvas" in clean_low
        ):
            return False, "HTML Validation Failed: No HTML or DOM root tags found."

        logs.append("HTML/DOM root markers detected; structural completeness is not yet proven.")
        if "<canvas" in clean_low:
            logs.append("Canvas 2D render surface detected.")
        if "<script" in clean_low and "</script>" in clean_low:
            logs.append(
                "Embedded JavaScript script block detected; syntax/runtime status is checked separately when tooling is available."
            )
            # Check script blocks for obvious syntax errors via node or py ast if python
            scripts = re.findall(
                r"<script[^>]*>(.*?)</script>", html_content, re.DOTALL | re.IGNORECASE
            )
            node_bin = shutil.which("node")
            if node_bin:
                for idx, sc in enumerate(scripts, start=1):
                    if sc.strip():
                        with tempfile.NamedTemporaryFile(
                            "w", suffix=".js", delete=False, encoding="utf-8"
                        ) as f:
                            f.write(sc)
                            temp_js = f.name
                        try:
                            res = subprocess.run(
                                [node_bin, "--check", temp_js],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                timeout=2.0,
                            )
                            if res.returncode != 0:
                                return (
                                    False,
                                    f"HTML <script> Block #{idx} Syntax Error:\n{res.stderr}",
                                )
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
