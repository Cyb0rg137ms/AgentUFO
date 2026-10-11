"""
ollama_launcher.py
==================
Auto-detection, daemon lifecycle management, model gallery, and background
pull streamer for local Ollama GGUF models in Agent-IDE Sandbox.

Enables zero-friction local AI execution when no cloud API keys are provided:
- Auto-detects Ollama installation (PATH + common Windows/macOS/Linux locations)
- Launches `ollama serve` daemon automatically if not already active
- Curates top open-source models (Gemma 2, Qwen 2.5, DeepSeek R1, LLaMA 3.2, etc.)
- Streams download and verification progress directly to frontend via SSE
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional


CURATED_MODELS: List[Dict[str, Any]] = [
    {
        "tag": "gemma2:2b",
        "name": "Gemma 2 (2B)",
        "provider": "Google",
        "size": "1.6 GB",
        "vram": "2.5 GB",
        "speed": "⚡ Ultra Fast",
        "description": "Google's lightweight model — ultra-low latency, ideal for quick iterations, testing, and responsive code assistance.",
        "badge": "Fastest",
        "recommended": True,
    },
    {
        "tag": "qwen2.5-coder:1.5b",
        "name": "Qwen 2.5 Coder (1.5B)",
        "provider": "Alibaba",
        "size": "986 MB",
        "vram": "1.8 GB",
        "speed": "⚡ Ultra Fast",
        "description": "Ultra-lightweight specialized code model — instant responses, perfect for rapid development and testing.",
        "badge": "Fastest Coder",
        "recommended": True,
    },
    {
        "tag": "gemma2:4b",
        "name": "Gemma 2 (4B)",
        "provider": "Google",
        "size": "2.6 GB",
        "vram": "3.5 GB",
        "speed": "⚡ Very Fast",
        "description": "Balanced Google model with enhanced precision, reasoning, and instruction-following for compact systems.",
        "badge": "Balanced",
        "recommended": False,
    },
    {
        "tag": "qwen2.5-coder:7b",
        "name": "Qwen 2.5 Coder (7B)",
        "provider": "Alibaba",
        "size": "4.7 GB",
        "vram": "6.0 GB",
        "speed": "🚀 Fast",
        "description": "Specialized code intelligence — excels at full-stack app design, bug finding, Python sandboxing & refactoring.",
        "badge": "Best for Code",
        "recommended": True,
    },
    {
        "tag": "qwen2.5:7b",
        "name": "Qwen 2.5 (7B)",
        "provider": "Alibaba",
        "size": "4.7 GB",
        "vram": "6.0 GB",
        "speed": "🚀 Fast",
        "description": "Top-tier open-source foundation model with state-of-the-art multilingual, coding, and reasoning benchmarks.",
        "badge": "Popular",
        "recommended": True,
    },
    {
        "tag": "deepseek-r1:7b",
        "name": "DeepSeek R1 (7B)",
        "provider": "DeepSeek",
        "size": "4.7 GB",
        "vram": "6.0 GB",
        "speed": "🧠 Deep Reasoning",
        "description": "Chain-of-thought reasoning powerhouse — excels at mathematical proofs, logic invariants, and system architecture.",
        "badge": "Reasoning",
        "recommended": False,
    },
    {
        "tag": "llama3.2:3b",
        "name": "Llama 3.2 (3B)",
        "provider": "Meta",
        "size": "2.0 GB",
        "vram": "3.0 GB",
        "speed": "⚡ Ultra Fast",
        "description": "Meta's efficient edge model — high throughput, fast conversational responses, low memory footprint.",
        "badge": "Lightweight",
        "recommended": False,
    },
    {
        "tag": "mistral:7b",
        "name": "Mistral (7B)",
        "provider": "Mistral AI",
        "size": "4.1 GB",
        "vram": "5.5 GB",
        "speed": "🚀 Fast",
        "description": "Industry benchmark for 7B models — robust instruction following, concise code synthesis and reasoning.",
        "badge": "Robust",
        "recommended": False,
    },
    {
        "tag": "phi3:mini",
        "name": "Phi-3 Mini (3.8B)",
        "provider": "Microsoft",
        "size": "2.2 GB",
        "vram": "3.2 GB",
        "speed": "⚡ Very Fast",
        "description": "Microsoft's high-efficiency reasoning engine trained on synthetic textbook quality data.",
        "badge": "Compact",
        "recommended": False,
    },
]


class OllamaAutoLauncher:
    """Manages Ollama binary detection, daemon process launch, model pull & gallery."""

    DEFAULT_HOST = "http://127.0.0.1:11434"

    def __init__(self, host: Optional[str] = None) -> None:
        self.host = (host or self.DEFAULT_HOST).rstrip("/")
        self._daemon_process: Optional[subprocess.Popen] = None

    @classmethod
    def find_binary(cls) -> Optional[str]:
        """Finds the Ollama executable on the system across PATH and platform-specific standard paths."""
        # 1. Standard PATH
        which_path = shutil.which("ollama")
        if which_path and os.path.exists(which_path):
            return which_path

        # 2. Windows specific paths
        if sys.platform == "win32":
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            candidates = [
                os.path.join(local_appdata, "Programs", "Ollama", "ollama.exe"),
                os.path.expanduser(r"~\AppData\Local\Programs\Ollama\ollama.exe"),
                r"C:\Program Files\Ollama\ollama.exe",
                r"C:\Program Files (x86)\Ollama\ollama.exe",
            ]
            for c in candidates:
                if c and os.path.isfile(c):
                    return c

        # 3. macOS / Linux paths
        posix_candidates = [
            "/usr/local/bin/ollama",
            "/opt/homebrew/bin/ollama",
            "/usr/bin/ollama",
            os.path.expanduser("~/.ollama/bin/ollama"),
        ]
        for c in posix_candidates:
            if os.path.isfile(c):
                return c

        return None

    def is_daemon_running(self, timeout: float = 2.0) -> bool:
        """Fast HTTP ping to /api/version to check if Ollama daemon is responsive."""
        try:
            req = urllib.request.Request(f"{self.host}/api/version", method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_version(self) -> Optional[str]:
        """Returns the version string reported by the Ollama daemon, if active."""
        try:
            req = urllib.request.Request(f"{self.host}/api/version", method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("version")
        except Exception:
            return None

    def start_daemon(self, timeout: float = 20.0) -> Dict[str, Any]:
        """
        Launches `ollama serve` in the background if it is not already running.
        Polls until the daemon is responsive or timeout occurs.
        """
        if self.is_daemon_running():
            version = self.get_version()
            return {
                "success": True,
                "already_running": True,
                "message": f"Ollama daemon is already active (version {version or 'unknown'}).",
                "version": version,
            }

        binary = self.find_binary()
        if not binary:
            return {
                "success": False,
                "error": "Ollama executable not found on this system.",
                "download_url": "https://ollama.com/download",
                "message": "Please install Ollama from https://ollama.com/download to enable local LLM inference.",
            }

        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

            # Spawn `ollama serve` detached
            self._daemon_process = subprocess.Popen(
                [binary, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                close_fds=(sys.platform != "win32"),
            )
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to launch Ollama process: {e}",
            }

        # Poll until daemon responds
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.5)
            if self.is_daemon_running(timeout=0.5):
                version = self.get_version()
                return {
                    "success": True,
                    "already_running": False,
                    "message": f"Ollama daemon started successfully (version {version or '0.x'}).",
                    "version": version,
                }

        return {
            "success": False,
            "error": "Ollama daemon was started but did not respond within timeout period.",
        }

    def list_installed(self) -> List[str]:
        """Returns all model tags currently installed in local Ollama storage."""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                tags = []
                for m in models:
                    name = m.get("name")
                    if name:
                        tags.append(name)
                        # Also add short tag if it ends with :latest
                        if name.endswith(":latest"):
                            tags.append(name[:-7])
                return tags
        except Exception:
            return []

    def get_gallery(self) -> List[Dict[str, Any]]:
        """Returns the curated model catalog with live `installed` status flags."""
        installed_tags = set(self.list_installed())
        gallery = []
        for item in CURATED_MODELS:
            tag = item["tag"]
            is_installed = (
                tag in installed_tags
                or f"{tag}:latest" in installed_tags
                or any(t == tag or t.startswith(tag + ":") for t in installed_tags)
            )
            item_copy = dict(item)
            item_copy["installed"] = is_installed
            gallery.append(item_copy)

        # Also add any installed models that aren't in CURATED_MODELS
        for it in self.list_installed():
            if ":" not in it and f"{it}:latest" in installed_tags:
                continue
            if not any(g["tag"] == it or it.startswith(g["tag"].split(":")[0]) for g in gallery):
                gallery.insert(0, {
                    "tag": it,
                    "name": f"Local: {it}",
                    "provider": "Installed",
                    "size": "Local Storage",
                    "vram": "Detected",
                    "speed": "⚡ Local Ready",
                    "description": f"Custom installed local model '{it}' ready for instant execution.",
                    "badge": "Ready",
                    "recommended": True,
                    "installed": True,
                })
        return gallery

    def get_status(self) -> Dict[str, Any]:
        """Returns full local AI status including binary presence, daemon state, and installed models."""
        binary = self.find_binary()
        running = self.is_daemon_running()
        version = self.get_version() if running else None
        installed_models = self.list_installed() if running else []

        return {
            "installed": binary is not None,
            "binary_path": binary,
            "running": running,
            "version": version,
            "host": self.host,
            "installed_models": installed_models,
            "models_count": len(installed_models),
            "gallery_count": len(CURATED_MODELS),
        }

    def pull_model_stream(self, model_tag: str) -> Iterator[Dict[str, Any]]:
        """
        Pulls a model from Ollama library, yielding real-time progress dicts.
        If daemon is not running, attempts to start it first.
        """
        # Ensure daemon is running
        if not self.is_daemon_running():
            launch_res = self.start_daemon()
            if not launch_res.get("success"):
                yield {
                    "status": "error",
                    "error": launch_res.get("error", "Could not start Ollama daemon"),
                    "done": True,
                    "percent": 0,
                }
                return
            yield {
                "status": "daemon_started",
                "message": "Ollama daemon auto-started.",
                "percent": 2,
                "done": False,
            }

        url = f"{self.host}/api/pull"
        payload = json.dumps({"name": model_tag, "stream": True}).encode("utf-8")

        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3600.0) as resp:
                for raw_line in resp:
                    if not raw_line:
                        continue
                    line_str = raw_line.decode("utf-8").strip()
                    if not line_str:
                        continue
                    try:
                        chunk = json.loads(line_str)
                    except json.JSONDecodeError:
                        continue

                    status = chunk.get("status", "")
                    completed = chunk.get("completed", 0)
                    total = chunk.get("total", 0)
                    digest = chunk.get("digest", "")

                    percent = 0.0
                    if total and total > 0:
                        percent = round((completed / total) * 100, 1)

                    is_done = (status == "success")

                    yield {
                        "status": status,
                        "model": model_tag,
                        "completed": completed,
                        "total": total,
                        "percent": percent,
                        "digest": digest,
                        "done": is_done,
                    }

                    if is_done:
                        break

        except Exception as e:
            # Fallback to CLI subprocess if HTTP pull has issues
            binary = self.find_binary()
            if not binary:
                yield {
                    "status": "error",
                    "error": f"Pull failed: {e}",
                    "done": True,
                    "percent": 0,
                }
                return

            try:
                proc = subprocess.Popen(
                    [binary, "pull", model_tag],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                for line in iter(proc.stdout.readline, ""):
                    line = line.strip()
                    if not line:
                        continue
                    # Regex match percentage: e.g. "45%"
                    pct_match = re.search(r"(\d+)%", line)
                    pct = float(pct_match.group(1)) if pct_match else 0.0
                    yield {
                        "status": line[:80],
                        "model": model_tag,
                        "percent": pct,
                        "done": False,
                    }
                proc.stdout.close()
                proc.wait()
                yield {
                    "status": "success",
                    "model": model_tag,
                    "percent": 100.0,
                    "done": True,
                }
            except Exception as cli_err:
                yield {
                    "status": "error",
                    "error": f"CLI pull fallback failed: {cli_err}",
                    "done": True,
                    "percent": 0,
                }
