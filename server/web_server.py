"""
web_server.py
=============
Multi-threaded HTTP Server for Agent-IDE Sandbox Web UI.
Serves static frontend assets and REST API endpoints without external dependencies.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
import urllib.parse

from server.handlers import ChatSessionManager
from server.crew_runtime import CrewRunManager


class AgentIDERequestHandler(BaseHTTPRequestHandler):
    """HTTP Request handler routing static assets and REST API endpoints."""

    session_manager = ChatSessionManager()
    crew_runs = CrewRunManager(session_manager)
    static_dir = Path(__file__).parent.parent / "web"
    timeout = 300.0

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        """Helper to send JSON responses."""
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, file_path: Path) -> None:
        """Helper to serve static files with correct MIME types."""
        # Static paths must stay under the web root; never let URL traversal read host files.
        root = self.static_dir.resolve()
        try:
            file_path = file_path.resolve(strict=True)
            if not file_path.is_relative_to(root) or not file_path.is_file():
                self.send_error(404, "File not found")
                return
        except (OSError, ValueError):
            self.send_error(404, "File not found")
            return

        mime_type, _ = mimetypes.guess_type(str(file_path))
        mime_type = mime_type or "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error reading file: {e}")

    def do_OPTIONS(self) -> None:
        """Handles CORS preflight."""
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        """Handles static file serving and GET API routes."""
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path).rstrip("/")
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        # Backward-compatible aliases for older frontend bundles.
        path = {
            "/api/files/tree": "/api/fs/tree",
            "/api/files/read": "/api/fs/read",
            "/api/artifacts/list": "/api/artifacts",
            "/api/agentufo/probes/run": "/api/agentufo/run",
        }.get(path, path)

        # Liveness/readiness endpoints are intentionally lightweight and machine-readable.
        if path in ("/healthz", "/api/health"):
            mgr = self.session_manager
            provider_available = bool(getattr(mgr.operator.llm, "is_available", lambda: False)())
            self._send_json({
                "status": "ok",
                "service": "agentufo",
                "version": "0.1.0-dev",
                "provider": getattr(mgr, "provider_name", "unknown"),
                "provider_available": provider_available,
                "setup_required": not provider_available,
            })
            return

        # Durable crew control-plane endpoints. Polling is intentionally simple and local-first.
        if path == "/api/crew/runs":
            self._send_json(self.crew_runs.list_runs())
            return
        elif path.startswith("/api/crew/runs/") and path.endswith("/events"):
            run_id = path[len("/api/crew/runs/"):-len("/events")].strip("/")
            try:
                after = max(0, int((query.get("after") or ["0"])[0]))
            except (TypeError, ValueError):
                after = 0
            result = self.crew_runs.events(run_id, after)
            self._send_json(result, status=404 if result.get("error") else 200)
            return
        elif path.startswith("/api/crew/runs/"):
            run_id = path[len("/api/crew/runs/"):].strip("/")
            result = self.crew_runs.get_run(run_id)
            self._send_json(result or {"error": "Run not found"}, status=200 if result else 404)
            return

        # API Endpoints
        if path == "/api/status":
            self._send_json(self.session_manager.get_status())
            return
        elif path == "/api/models":
            self._send_json(self.session_manager.get_models())
            return
        elif path == "/api/chats":
            self._send_json(self.session_manager.get_chats())
            return
        elif path == "/api/chats/load":
            chat_id = query.get("id", [""])[0]
            chat_data = self.session_manager.load_chat(chat_id)
            if chat_data:
                self._send_json(chat_data)
            else:
                self._send_json({"error": "Chat not found"}, status=404)
            return
        elif path == "/api/projects":
            self._send_json(self.session_manager.get_projects())
            return
        elif path == "/api/fs/tree":
            root = (query.get("dir") or query.get("path") or query.get("folder") or [None])[0]
            self._send_json(self.session_manager.get_project_tree(root_dir=root))
            return
        elif path == "/api/fs/read":
            file_path = (query.get("path") or [""])[0]
            self._send_json(self.session_manager.read_project_file(file_path))
            return
        elif path == "/api/artifacts":
            self._send_json(self.session_manager.get_artifacts())
            return
        elif path == "/api/hybrid/status":
            self._send_json(self.session_manager.get_hybrid_status())
            return
        elif path in ("/api/swarm/status", "/api/agentufo/status", "/api/agentufo/workspace"):
            self._send_json(self.session_manager.get_agentufo_status())
            return
        elif path == "/api/agentufo/mailbox":
            self._send_json(self.session_manager.get_agentufo_mailbox())
            return
        elif path == "/api/agentufo/locks":
            self._send_json(self.session_manager.get_agentufo_locks())
            return
        elif path == "/api/agentufo/cockpit":
            self._send_json(self.session_manager.get_agentufo_cockpit())
            return
        elif path == "/api/agentufo/prober/status":
            self._send_json(self.session_manager.get_agentufo_prober_status())
            return
        elif path == "/api/agentufo/models/ranked":
            self._send_json(self.session_manager.rank_agentufo_models())
            return
        elif path == "/api/mcp/presets":
            self._send_json(self.session_manager.get_mcp_presets())
            return
        elif path == "/api/ollama/status":
            self._send_json(self.session_manager.get_ollama_status())
            return
        elif path == "/api/ollama/gallery":
            self._send_json(self.session_manager.get_ollama_gallery())
            return
        elif path == "/api/ollama/pull":
            model_name = (query.get("model") or [""])[0]
            if not model_name:
                self._send_json({"error": "Missing model parameter"}, status=400)
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            try:
                for event in self.session_manager.pull_ollama_model(model_name):
                    data = json.dumps(event)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    if event.get("done"):
                        break
            except Exception as e:
                err_data = json.dumps({"status": "error", "error": str(e), "done": True})
                try:
                    self.wfile.write(f"data: {err_data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass
            return

        # Static Assets
        if path == "" or path == "/":
            self._send_file(self.static_dir / "index.html")
            return

        rel_path = path.lstrip("/")
        target_file = (self.static_dir / rel_path).resolve()
        try:
            inside_web_root = target_file.is_relative_to(self.static_dir.resolve())
        except ValueError:
            inside_web_root = False
        if inside_web_root and target_file.exists() and target_file.is_file():
            self._send_file(target_file)
        elif Path(rel_path).suffix or rel_path.startswith(("css/", "js/")):
            self.send_error(404, "Static asset not found")
        else:
            # Fallback to index.html for extension-less app routes only.
            self._send_file(self.static_dir / "index.html")

    def do_POST(self) -> None:
        """Handles API POST routes."""
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).rstrip("/")
        path = {"/api/agentufo/probes/run": "/api/agentufo/run"}.get(path, path)
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            self._send_json({"error": "Invalid Content-Length header"}, status=400)
            return
        max_body_bytes = 10 * 1024 * 1024
        if content_length < 0 or content_length > max_body_bytes:
            self._send_json({"error": f"Request body must be at most {max_body_bytes // (1024 * 1024)} MiB"}, status=413)
            return
        try:
            post_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            payload = json.loads(post_body)
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json({"error": f"Invalid JSON request body: {exc}"}, status=400)
            return

        if path == "/api/crew/run":
            result = self.crew_runs.create(payload)
            self._send_json(result, status=202 if result.get("status") == "accepted" else 400)
            return

        elif path == "/api/crew/control":
            result = self.crew_runs.control(payload)
            self._send_json(result, status=400 if result.get("status") == "error" else 200)
            return

        elif path == "/api/crew/configure":
            result = self.session_manager.configure_agentufo(payload)
            self._send_json(result)
            return

        if path == "/api/chat":
            prompt = payload.get("prompt", "").strip()
            if not prompt:
                self._send_json({"error": "Prompt cannot be empty"}, status=400)
                return
            result = self.session_manager.process_chat(
                prompt,
                enable_hybrid=payload.get("enable_hybrid"),
                swarm_mode=payload.get("swarm_mode"),
                swarm_agents=payload.get("swarm_agents"),
                swarm_policy=payload.get("swarm_policy"),
            )
            self._send_json(result)

        elif path == "/api/chat/stream":
            prompt = payload.get("prompt", "").strip()
            if not prompt:
                self._send_json({"error": "Prompt cannot be empty"}, status=400)
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def on_progress(evt):
                try:
                    payload_evt = json.dumps({"type": "progress", "event": evt.to_dict() if hasattr(evt, "to_dict") else evt})
                    self.wfile.write(f"data: {payload_evt}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass

            try:
                result = self.session_manager.process_chat(
                    prompt,
                    progress_callback=on_progress,
                    enable_hybrid=payload.get("enable_hybrid"),
                    swarm_mode=payload.get("swarm_mode"),
                    swarm_agents=payload.get("swarm_agents"),
                    swarm_policy=payload.get("swarm_policy"),
                )
                final_payload = json.dumps({"type": "final", "data": result})
                self.wfile.write(f"data: {final_payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception as e:
                err_payload = json.dumps({"type": "error", "error": str(e)})
                try:
                    self.wfile.write(f"data: {err_payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass
            return

        elif path == "/api/hybrid/configure":
            res = self.session_manager.configure_hybrid(payload)
            self._send_json(res)

        elif path in ("/api/swarm/configure", "/api/agentufo/configure"):
            res = self.session_manager.configure_agentufo(payload)
            self._send_json(res)

        elif path == "/api/agentufo/run":
            prompt = (payload.get("prompt") or payload.get("task") or "").strip()
            if not prompt:
                self._send_json({"error": "Prompt or task cannot be empty"}, status=400)
                return
            try:
                res = self.session_manager.run_agentufo_mission(prompt)
                self._send_json(res)
            except Exception as e:
                self._send_json({
                    "status": "error",
                    "review_passed": False,
                    "error": str(e),
                    "error_type": type(e).__name__,
                }, status=500)

        elif path == "/api/agentufo/seat/action":
            seat_id = payload.get("seat_id", "")
            action = payload.get("action", "")
            context = payload.get("context", "")
            res = self.session_manager.execute_seat_action(seat_id, action, context)
            self._send_json(res)

        elif path == "/api/agentufo/locks/break":
            target_path = payload.get("target_path") or payload.get("path") or ""
            breaker = payload.get("breaker_id") or "master_admin"
            reason = payload.get("reason") or "UI Override"
            res = self.session_manager.break_agentufo_lock(target_path, breaker_id=breaker, reason=reason)
            self._send_json(res)

        elif path == "/api/agentufo/paths/claim":
            seat_id = payload.get("seat_id") or "seat_2_builder"
            paths = payload.get("paths") or []
            role = payload.get("seat_role") or "builder"
            lease = float(payload.get("lease_seconds", 600.0))
            res = self.session_manager.claim_agentufo_paths(seat_id, paths, seat_role=role, lease_seconds=lease)
            self._send_json(res)

        elif path == "/api/agentufo/brief/handoff":
            from_seat = payload.get("from_seat") or "seat_1_architect"
            to_seat = payload.get("to_seat") or "seat_2_builder"
            title = payload.get("title") or "Inter-Seat Brief"
            body = payload.get("body") or ""
            btype = payload.get("brief_type") or "handoff"
            res = self.session_manager.handoff_agentufo_brief(from_seat, to_seat, title, body, brief_type=btype)
            self._send_json(res)

        elif path in ("/api/projects/open", "/api/fs/open_project"):
            folder = payload.get("folder_path") or payload.get("path") or ""
            res = self.session_manager.open_project(folder)
            self._send_json(res)

        elif path == "/api/agentufo/fleet/configure":
            res = self.session_manager.configure_agentufo(payload)
            self._send_json(res)

        elif path == "/api/settings":
            updated_status = self.session_manager.update_settings(payload)
            self._send_json(updated_status)

        elif path == "/api/clear":
            result = self.session_manager.clear_session()
            self._send_json(result)

        elif path == "/api/chats/save":
            res = self.session_manager.save_chat(payload)
            self._send_json(res)

        elif path == "/api/projects/create":
            res = self.session_manager.create_project(payload)
            self._send_json(res)

        elif path == "/api/mcp/generate":
            res = self.session_manager.generate_mcp_server(payload)
            self._send_json(res)

        elif path == "/api/mcp/test":
            res = self.session_manager.test_mcp_tool(payload)
            self._send_json(res)

        elif path == "/api/jev/decide":
            prompt = payload.get("prompt", "")
            res = self.session_manager.evaluate_jev(prompt)
            self._send_json(res)

        elif path == "/api/models/test":
            res = self.session_manager.test_connection()
            self._send_json(res)

        elif path == "/api/ollama/launch":
            res = self.session_manager.start_ollama_daemon()
            self._send_json(res)

        elif path == "/api/ollama/connect":
            model = payload.get("model", "gemma2:2b")
            res = self.session_manager.connect_ollama_model(model)
            self._send_json(res)

        elif path == "/api/fs/open_project":
            folder = payload.get("folder_path", "")
            res = self.session_manager.open_project(folder)
            self._send_json(res)

        elif path == "/api/fs/write":
            fpath = payload.get("path", "")
            content = payload.get("content", "")
            res = self.session_manager.write_project_file(fpath, content)
            self._send_json(res)

        elif path == "/api/detect_key":
            key = payload.get("api_key") or payload.get("key") or ""
            res = self.session_manager.detect_api_key(key)
            self._send_json(res)

        else:
            self.send_error(404, "Endpoint not found")

    def log_message(self, format: str, *args: Any) -> None:
        """Suppresses default noisy access logs unless error."""
        try:
            if args and len(args) > 1 and str(args[1]) in ["404", "500"]:
                super().log_message(format, *args)
        except Exception:
            pass


class AgentIDEServer:
    """Server wrapper for ThreadingHTTPServer."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8000, provider: Optional[str] = None, model: Optional[str] = None) -> None:
        self.host = host
        self.port = port
        self.httpd: Optional[ThreadingHTTPServer] = None
        # CLI provider/model options now configure the actual runtime instead of only changing the banner.
        if (provider and provider.lower() != "auto") or model:
            settings: Dict[str, Any] = {}
            if provider and provider.lower() != "auto":
                settings["provider"] = provider
            if model:
                settings["model"] = model
            result = AgentIDERequestHandler.session_manager.update_settings(settings)
            if result.get("status") == "error":
                raise ValueError(result["error"])

    def start(self) -> None:
        import socket
        server_address = (self.host, self.port)
        self.httpd = ThreadingHTTPServer(server_address, AgentIDERequestHandler)
        
        # Probe local Ollama status
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        port_open = s.connect_ex(("127.0.0.1", 11434)) == 0
        s.close()
        status_str = "OPEN - Running" if port_open else "CLOSED"
        mgr = AgentIDERequestHandler.session_manager
        model_str = mgr.model_name
        prov_str = mgr.provider_name

        print("============================================================")
        print("AGENT-IDE SANDBOX 2.0 (PRODUCTION HYBRID + BRIDGEMIND SWARM)")
        print(f"URL: http://{self.host}:{self.port}")
        print(f"Active Provider: {prov_str.upper()} | Model: {model_str}")
        print(f"Ollama Local (11434): {status_str}")
        print(f"Save Tokens Mode: {'ENABLED' if mgr.enable_hybrid else 'DISABLED'}")
        print("============================================================")
        print("[SERVER] Press Ctrl+C to stop.\n")
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[SERVER] Shutting down...")
        finally:
            self.httpd.server_close()
            self.httpd = None


def start_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = AgentIDEServer(host=host, port=port)
    server.start()


Server = AgentIDEServer
