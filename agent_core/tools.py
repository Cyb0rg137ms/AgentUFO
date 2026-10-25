"""
tools.py
========
Tool definitions and executor for the Agent-IDE Sandbox.

Implements an OpenAI function-calling compatible tool system:
  - FileReadTool: reads file content with optional line slicing
  - FileWriteTool: writes content to file path
  - FileListTool: lists directory contents with file sizes and types
  - FileSearchTool: searches files for text or regex patterns
  - DirectoryCreateTool: creates directories recursively
  - WebSearchTool: live web search via DuckDuckGo / knowledge corpus
  - BrowserFetchTool: fetches and converts webpage content to readable text
  - MultiLangSandboxTool: executes code in isolated sandbox (Python, JS, TS, PS, HTML)
  - ShellExecuteTool: runs commands in sandboxed subprocess with timeout
  - PythonEvalTool: executes Python expressions safely with restricted globals
  - InvariantVerificationTool: verifies invariants and security boundaries
  - PonderingQueryTool: executes multi-angle hypothesis search
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Tool result container
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    """Structured result from a tool call."""

    tool_name: str
    success: bool
    output: str
    error: Optional[str] = None
    elapsed_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool_name,
            "success": self.success,
            "output": self.output[:4000] if self.output else "",
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:
        status = "✓" if self.success else "✗"
        return f"ToolResult({status} {self.tool_name}: {self.output[:80]!r})"


# ---------------------------------------------------------------------------
# Base tool
# ---------------------------------------------------------------------------


class BaseTool:
    """Abstract base for all agent tools."""

    name: str = "base"
    description: str = ""
    schema: Dict[str, Any] = {}

    def execute(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError

    def get_schema(self) -> Dict[str, Any]:
        """Returns OpenAI function-calling compatible schema."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.schema,
        }


# ---------------------------------------------------------------------------
# File & Directory Tools
# ---------------------------------------------------------------------------


class FileReadTool(BaseTool):
    """Reads the content of a file at a given path with optional line ranges."""

    name = "file_read"
    description = (
        "Read the contents of a text file. Supports optional start_line and end_line parameters."
    )
    schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the file to read.",
            },
            "start_line": {
                "type": "integer",
                "description": "Optional 1-indexed start line to read from.",
            },
            "end_line": {
                "type": "integer",
                "description": "Optional 1-indexed end line to read to.",
            },
            "max_lines": {
                "type": "integer",
                "description": "Maximum lines to read before truncation.",
            },
        },
        "required": ["path"],
    }

    def execute(
        self,
        path: str,
        max_lines: int = 1000,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> ToolResult:
        t0 = time.perf_counter()
        try:
            target = Path(path).resolve()
            if not target.exists():
                return ToolResult(
                    tool_name=self.name,
                    success=False,
                    output="",
                    error=f"File not found: {path}",
                    elapsed_ms=(time.perf_counter() - t0) * 1000,
                )
            if not target.is_file():
                return ToolResult(
                    tool_name=self.name,
                    success=False,
                    output="",
                    error=f"Path is a directory, not a file: {path}",
                    elapsed_ms=(time.perf_counter() - t0) * 1000,
                )

            content = target.read_text(encoding="utf-8", errors="replace")
            all_lines = content.splitlines(keepends=True)

            if len(all_lines) > max_lines:
                output = (
                    "".join(all_lines[:max_lines])
                    + f"\n[... truncated {len(all_lines) - max_lines} lines ...]"
                )
            elif start_line is not None or end_line is not None:
                start = max(0, (start_line or 1) - 1)
                end = end_line if end_line is not None else len(all_lines)
                sliced = "".join(all_lines[start:end])
                output = f"[Showing lines {start + 1} to {min(end, len(all_lines))} of {len(all_lines)}]\n{sliced}"
            else:
                output = content

            return ToolResult(
                tool_name=self.name,
                success=True,
                output=output,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={"total_lines": len(all_lines), "byte_size": target.stat().st_size},
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=f"Failed to read file: {e}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


class FileWriteTool(BaseTool):
    """Writes content to a file at a given path, creating directories as needed."""

    name = "file_write"
    description = (
        "Write text content to a file. Automatically creates any missing parent directories."
    )
    schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "Text content to write to the file.",
            },
            "mode": {
                "type": "string",
                "enum": ["write", "append"],
                "description": "Write mode: 'write' to overwrite, 'append' to append.",
            },
        },
        "required": ["path", "content"],
    }

    def execute(self, path: str, content: str, mode: str = "write") -> ToolResult:
        t0 = time.perf_counter()
        try:
            target = Path(path).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "append":
                with open(target, "a", encoding="utf-8") as f:
                    f.write(content)
            else:
                target.write_text(content, encoding="utf-8")
            bytes_written = len(content.encode("utf-8"))
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=f"Successfully wrote {bytes_written} bytes to {target}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={"path": str(target), "bytes": bytes_written},
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=f"Failed to write file: {e}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


class FileListTool(BaseTool):
    """Lists directory contents with file metadata and directory trees."""

    name = "file_list"
    description = "List files and subdirectories at a given directory path with sizes and types."
    schema = {
        "type": "object",
        "properties": {
            "directory": {
                "type": "string",
                "description": "Directory path to list (defaults to current working directory).",
            },
            "recursive": {
                "type": "boolean",
                "description": "Whether to list subdirectories recursively (default false).",
            },
            "max_items": {
                "type": "integer",
                "description": "Maximum number of items to return (default 100).",
            },
        },
    }

    def execute(
        self, directory: str = ".", recursive: bool = False, max_items: int = 100
    ) -> ToolResult:
        t0 = time.perf_counter()
        try:
            target = Path(directory).resolve()
            if not target.exists() or not target.is_dir():
                return ToolResult(
                    tool_name=self.name,
                    success=False,
                    output="",
                    error=f"Directory not found: {directory}",
                    elapsed_ms=(time.perf_counter() - t0) * 1000,
                )

            items = []
            iterator = target.rglob("*") if recursive else target.iterdir()
            count = 0
            for p in iterator:
                # Skip hidden/cache folders
                if any(
                    part.startswith(".") or part in ("node_modules", "__pycache__", "venv")
                    for part in p.parts
                ):
                    continue
                count += 1
                if count > max_items:
                    break
                rel = p.relative_to(target)
                if p.is_dir():
                    items.append(f"📁 {rel}/")
                else:
                    sz = p.stat().st_size
                    sz_str = f"{sz} B" if sz < 1024 else f"{sz / 1024:.1f} KB"
                    items.append(f"📄 {rel} ({sz_str})")

            summary = f"Directory: {target} ({len(items)} items):\n" + "\n".join(items)
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=summary,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={"total_items": len(items)},
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=f"Failed to list directory: {e}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


class FileSearchTool(BaseTool):
    """Searches files in a directory for text or regex patterns."""

    name = "file_search"
    description = "Search for a query pattern or keyword across files in a directory."
    schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Text substring or regex pattern to search for.",
            },
            "directory": {
                "type": "string",
                "description": "Directory to search within (defaults to current working directory).",
            },
            "file_pattern": {
                "type": "string",
                "description": "Optional glob filter for filenames (e.g. '*.py', '*.js').",
            },
            "max_matches": {
                "type": "integer",
                "description": "Maximum number of matching lines to return (default 50).",
            },
        },
        "required": ["query"],
    }

    def execute(
        self, query: str, directory: str = ".", file_pattern: str = "*", max_matches: int = 50
    ) -> ToolResult:
        t0 = time.perf_counter()
        try:
            target = Path(directory).resolve()
            matches = []
            pattern = re.compile(query, re.IGNORECASE)

            for p in target.rglob(file_pattern):
                if any(
                    part.startswith(".") or part in ("node_modules", "__pycache__", "venv")
                    for part in p.parts
                ):
                    continue
                if p.is_file():
                    try:
                        text = p.read_text(encoding="utf-8", errors="ignore")
                        for idx, line in enumerate(text.splitlines(), start=1):
                            if pattern.search(line):
                                rel = p.relative_to(target)
                                matches.append(f"{rel}:{idx}: {line.strip()[:150]}")
                                if len(matches) >= max_matches:
                                    break
                    except Exception:
                        pass
                if len(matches) >= max_matches:
                    break

            out = f"Search '{query}' in {target} ({len(matches)} matches):\n" + "\n".join(matches)
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=out,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={"match_count": len(matches)},
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=f"Search failed: {e}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


class DirectoryCreateTool(BaseTool):
    """Creates a new directory folder."""

    name = "directory_create"
    description = "Create a directory folder path (recursively)."
    schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path to create.",
            },
        },
        "required": ["path"],
    }

    def execute(self, path: str) -> ToolResult:
        t0 = time.perf_counter()
        try:
            target = Path(path).resolve()
            target.mkdir(parents=True, exist_ok=True)
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=f"Created directory: {target}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=f"Failed to create directory: {e}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


# ---------------------------------------------------------------------------
# Internet & Web Tools
# ---------------------------------------------------------------------------


class WebSearchTool(BaseTool):
    """Searches external web or algorithmic corpus for theorems, invariants, and specs."""

    name = "web_search"
    description = (
        "Search the web or knowledge corpus for algorithmic invariants, documentation, and specs."
    )
    schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to look up.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to retrieve (default 3).",
            },
        },
        "required": ["query"],
    }

    def execute(self, query: str, max_results: int = 3) -> ToolResult:
        t0 = time.perf_counter()
        try:
            from agent_core.logos.dynamic_retrieval import DynamicAxiomRetriever

            retriever = DynamicAxiomRetriever()
            results = retriever.search_duckduckgo(query, max_results=max_results)
            output_parts = [f"Found {len(results)} references:"]
            for r in results:
                output_parts.append(f"- [{r.title}] ({r.url}): {r.snippet}")
            return ToolResult(
                tool_name=self.name,
                success=True,
                output="\n".join(output_parts),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={"count": len(results)},
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=str(e),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


class BrowserFetchTool(BaseTool):
    """Fetches a webpage URL and extracts clean readable markdown/text."""

    name = "browser_fetch"
    description = (
        "Fetch webpage content from a URL via HTTP request and return clean readable text."
    )
    schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The HTTP or HTTPS URL to fetch.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default 4000).",
            },
        },
        "required": ["url"],
    }

    def execute(self, url: str, max_chars: int = 4000) -> ToolResult:
        t0 = time.perf_counter()
        try:
            if not url.startswith("http://") and not url.startswith("https://"):
                url = "https://" + url

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,text/plain",
            }
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                raw_bytes = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                html_text = raw_bytes.decode(charset, errors="replace")

            # Strip script and style tags
            clean = re.sub(
                r"<(script|style|noscript|header|footer|nav)[^>]*>.*?</\1>",
                " ",
                html_text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            # Convert basic tags to whitespace / newlines
            clean = re.sub(r"<br\s*/?>", "\n", clean, flags=re.IGNORECASE)
            clean = re.sub(r"</?(p|div|h[1-6]|li|tr)[^>]*>", "\n", clean, flags=re.IGNORECASE)
            clean = re.sub(r"<[^>]+>", " ", clean)
            # Normalize whitespace
            clean = re.sub(r"[ \t]+", " ", clean)
            clean = re.sub(r"\n\s*\n+", "\n\n", clean).strip()

            truncated = clean[:max_chars]
            if len(clean) > max_chars:
                truncated += f"\n\n[... Truncated from total {len(clean)} characters ...]"

            return ToolResult(
                tool_name=self.name,
                success=True,
                output=f"URL Content for <{url}>:\n\n{truncated}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={"url": url, "original_length": len(clean)},
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=f"Browser fetch failed for '{url}': {e}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


# ---------------------------------------------------------------------------
# Multi-Language Sandbox Tool
# ---------------------------------------------------------------------------


class MultiLangSandboxTool(BaseTool):
    """Executes code across Python, JavaScript, TypeScript, PowerShell, or HTML."""

    name = "sandbox_execute"
    description = "Execute code inside an isolated sandbox environment. Supports Python, Node.js, TypeScript, PowerShell, and HTML validation."
    schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Source code to execute or validate.",
            },
            "runtime": {
                "type": "string",
                "enum": ["python", "node", "typescript", "powershell", "bash", "html", "auto"],
                "description": "Target execution runtime (default 'auto').",
            },
            "filename": {
                "type": "string",
                "description": "Optional target filename inside sandbox.",
            },
        },
        "required": ["code"],
    }

    def execute(
        self, code: str, runtime: str = "auto", filename: Optional[str] = None
    ) -> ToolResult:
        t0 = time.perf_counter()
        try:
            from agent_core.sandbox import CodeExecutionSandbox

            sandbox = CodeExecutionSandbox(timeout_seconds=6.0)
            rt = None if runtime == "auto" else runtime
            fn = filename or (
                "script.py"
                if rt == "python"
                else "script.js"
                if rt in ("node", "javascript")
                else "temp_script"
            )
            success, logs = sandbox.execute_script(code, filename=fn, runtime=rt)
            return ToolResult(
                tool_name=self.name,
                success=success,
                output=logs,
                error=None if success else "Execution encountered errors or assertions failed.",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=f"Sandbox execution error: {e}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


# ---------------------------------------------------------------------------
# Shell & Python Eval Tools
# ---------------------------------------------------------------------------


class ShellExecuteTool(BaseTool):
    """Executes a command line instruction inside a subprocess."""

    name = "shell_execute"
    description = "Run a terminal shell command. Returns stdout and stderr logs."
    schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Command string to execute.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the command.",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds (default 10).",
            },
        },
        "required": ["command"],
    }

    MAX_TIMEOUT: float = 30.0

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        timeout_s: Optional[float] = None,
    ) -> ToolResult:
        t0 = time.perf_counter()
        actual_timeout = (
            timeout_s if timeout_s is not None else (timeout if timeout is not None else 10.0)
        )
        actual_timeout = min(actual_timeout, self.MAX_TIMEOUT)

        # Host shell access is a privileged capability. Deny it unless an operator explicitly
        # opts into unsafe local development mode; a substring blocklist is not a sandbox.
        if os.environ.get("AGENTUFO_ALLOW_HOST_SHELL") != "1":
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error="Host shell execution is disabled. Configure an isolated container-backed tool or explicitly opt in with AGENTUFO_ALLOW_HOST_SHELL=1 for local development only.",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

        # Defense-in-depth blocklist for the explicitly opted-in development path.
        blocklist = ["rm -rf /", "format c:", "del /f /s /q c:"]
        if any(b in command.lower() for b in blocklist):
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error="Blocked dangerous shell command.",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

        try:
            res = subprocess.run(
                command,
                shell=True,
                cwd=cwd or os.getcwd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=actual_timeout,
            )
            logs = res.stdout + res.stderr
            return ToolResult(
                tool_name=self.name,
                success=(res.returncode == 0),
                output=logs,
                error=None if res.returncode == 0 else f"Process exited with code {res.returncode}",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=f"Command timed out after {actual_timeout}s",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=str(e),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


class PythonEvalTool(BaseTool):
    """Safely evaluates pure Python mathematical and logic expressions."""

    name = "python_eval"
    description = "Safely evaluate a mathematical or logic Python expression."
    schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The Python expression to evaluate (e.g. '2 ** 32 - 1').",
            },
        },
        "required": ["expression"],
    }

    def execute(self, expression: str) -> ToolResult:
        t0 = time.perf_counter()
        try:
            tree = ast.parse(expression, mode="eval")
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    raise ValueError("Import statements are forbidden.")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in ("__import__", "exec", "eval", "open"):
                        raise ValueError(f"Function '{node.func.id}' is blocked")

            safe_globals = {
                "__builtins__": {},
                "abs": abs,
                "round": round,
                "len": len,
                "min": min,
                "max": max,
                "sum": sum,
                "range": range,
                "list": list,
                "dict": dict,
                "set": set,
                "tuple": tuple,
                "str": str,
                "int": int,
                "float": float,
                "bool": bool,
                "sorted": sorted,
                "enumerate": enumerate,
                "zip": zip,
                "math": __import__("math"),
            }
            res = eval(compile(tree, "<expression>", "eval"), safe_globals)
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=repr(res),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=str(e),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


# ---------------------------------------------------------------------------
# LOGOS Cognitive & Invariant Tools
# ---------------------------------------------------------------------------


class InvariantVerificationTool(BaseTool):
    """Verifies invariant adherence and boundary contracts on code."""

    name = "invariant_verify"
    description = "Verify logical consistency, zero-divisor guards, index bounds, and security boundaries on code."
    schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Code snippet to audit.",
            },
            "task_description": {
                "type": "string",
                "description": "The task specification to audit against.",
            },
        },
        "required": ["code"],
    }

    def execute(
        self,
        code: str = "",
        task_description: str = "invariant audit",
        propositions: Optional[List[str]] = None,
    ) -> ToolResult:
        t0 = time.perf_counter()
        try:
            from agent_core.logos.recursive_auditor import RigorousAuditor

            auditor = RigorousAuditor()
            target_code = code if code else "\n".join(propositions or [])
            report = auditor.audit(target_code, task_description)
            out = (
                f"Invariant Audit: {'PASS' if report.is_converged else 'ISSUES DETECTED'} (Score: {report.score}/10)\n"
                f"Passed Invariants:\n"
                + "\n".join(f"  ✓ {p}" for p in report.passed_invariants)
                + (
                    "\nDetected Issues:\n" + "\n".join(f"  ✗ {i}" for i in report.detected_issues)
                    if report.detected_issues
                    else ""
                )
            )
            return ToolResult(
                tool_name=self.name,
                success=report.is_converged,
                output=out,
                error=None if report.is_converged else "Invariant issues detected",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata=report.to_dict(),
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=str(e),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


class PonderingQueryTool(BaseTool):
    """Performs multi-angle pondering and hypothesis search."""

    name = "pondering_query"
    description = "Query the LOGOS Pondering Engine for horizontal multi-angle hypothesis search and confidence bounds."
    schema = {
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "The task or subproblem to ponder horizontally.",
            },
        },
        "required": ["task_description"],
    }

    def execute(self, task_description: str) -> ToolResult:
        t0 = time.perf_counter()
        try:
            from agent_core.logos.pondering_engine import PonderingEngine

            engine = PonderingEngine()
            synthesis = engine.evaluate_multi_angle(task_description)
            lines = [synthesis.synthesis_summary, "\nQualified Properties (Confidence >= 95%):"]
            for p in synthesis.qualified_properties:
                lines.append(
                    f"  * [{p.name}] (heuristic score={p.confidence:.2f}, uncalibrated): {p.hypothesis}"
                )
            return ToolResult(
                tool_name=self.name,
                success=True,
                output="\n".join(lines),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={
                    "qualified_count": synthesis.qualified_properties_count,
                    "remaining_space": synthesis.remaining_search_space_fraction,
                },
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="",
                error=str(e),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Registry of available tools, with lookup by name and schema export."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def schemas(self) -> List[Dict[str, Any]]:
        return [t.get_schema() for t in self._tools.values()]

    def execute(self, tool_name: str, **kwargs: Any) -> ToolResult:
        tool = self.get(tool_name)
        if tool is None:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"Unknown tool: {tool_name!r}. Available: {list(self._tools.keys())}",
            )
        return tool.execute(**kwargs)

    @classmethod
    def default(cls) -> "ToolRegistry":
        """Returns a registry pre-populated with all standard tools."""
        registry = cls()
        registry.register(FileReadTool())
        registry.register(FileWriteTool())
        registry.register(FileListTool())
        registry.register(FileSearchTool())
        registry.register(DirectoryCreateTool())
        registry.register(WebSearchTool())
        registry.register(BrowserFetchTool())
        registry.register(MultiLangSandboxTool())
        registry.register(ShellExecuteTool())
        registry.register(PythonEvalTool())
        inv_tool = InvariantVerificationTool()
        registry.register(inv_tool)
        # Register backward-compatible alias 'axiom_verify'
        alias_tool = InvariantVerificationTool()
        alias_tool.name = "axiom_verify"
        registry.register(alias_tool)
        registry.register(PonderingQueryTool())
        return registry
