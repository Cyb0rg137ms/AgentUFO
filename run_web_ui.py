from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from server.web_server import AgentIDERequestHandler, AgentIDEServer


def main():
    parser = argparse.ArgumentParser(description="Agent-IDE Sandbox & LOGOS Web Chatbot UI")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--provider",
        type=str,
        default="auto",
        help="Provider name, or 'auto' to detect configured providers",
    )
    parser.add_argument(
        "--model", type=str, default=None, help="Optional model tag/name for the selected provider"
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        server = AgentIDEServer(
            host=args.host, port=args.port, provider=args.provider, model=args.model
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    url_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{url_host}:{args.port}"
    manager = AgentIDERequestHandler.session_manager
    print("=" * 65)
    print("   AGENTUFO STUDIO — LOCAL-FIRST ENGINEERING WORKSPACE")
    print(f"   URL: {url}")
    print(f"   Provider: {manager.provider_name} / {manager.model_name}")
    if manager.provider_name == "unconfigured":
        print("   Setup required: start Ollama or configure an API key in Settings / .env")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "   WARNING: non-loopback bind requested; do not expose this unauthenticated server to untrusted networks."
        )
    print("   Press Ctrl+C to terminate.")
    print("=" * 65)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.start()
    except OSError as exc:
        print(f"Could not start server on {args.host}:{args.port}: {exc}", file=sys.stderr)
        print("Choose another --port or stop the process already using this port.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
