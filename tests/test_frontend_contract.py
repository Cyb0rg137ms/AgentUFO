from __future__ import annotations
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]

class FrontendContractTests(unittest.TestCase):
    def test_control_room_has_live_task_controls(self):
        app = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
        for marker in ("btn-launch-run", "btn-pause-run", "btn-stop-run", "crew-list", "timeline", "file-editor"):
            self.assertIn(marker, app)
        for marker in ("/api/crew/run", "/api/crew/control", "/api/crew/runs", "selected_models", "token_cap"):
            self.assertIn(marker, js)

    def test_multi_model_selection_is_applied_to_backend(self):
        js = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("state.selected", js)
        self.assertIn("/api/crew/configure", js)
        self.assertIn("selected_models:selected", js)

    def test_provider_detection_and_ollama_controls_exist(self):
        app = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
        for marker in ("btn-detect-key", "btn-start-ollama", "btn-connect-ollama"):
            self.assertIn(marker, app)
        self.assertIn("/api/detect_key", js)
        self.assertIn("/api/ollama/connect", js)

    def test_workspace_is_real_filesystem_not_browser_only(self):
        js = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/fs/tree", js)
        self.assertIn("/api/fs/read", js)
        self.assertIn("/api/fs/write", js)


    def test_additive_utility_windows_exist_without_removing_main_views(self):
        app = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
        for marker in ("window-routing", "window-crew", "window-control", "window-mcp", "utility-backdrop"):
            self.assertIn(marker, app)
        for marker in ("openUtilityWindow", "window-apply-routing", "/api/mcp/generate", "/api/mcp/test", "mcp-save-project"):
            self.assertIn(marker, js)
        for marker in ("view-control", "view-models", "view-workspace", "view-settings"):
            self.assertIn(marker, app)


    def test_chat_is_the_default_surface_and_routes_chat_vs_build_prompts(self):
        app = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
        for marker in ("id=\"view-chat\"", "chat-active-model", "chat-prompt", "chat-messages", "chat-send", "window-routing", "window-crew", "window-control", "window-mcp"):
            self.assertIn(marker, app)
        for marker in ("/api/chat/stream", "isBuildPrompt", "renderChatRun", "state.chatTokenSaving"):
            self.assertIn(marker, js)

    def test_mcp_window_can_save_generated_server_into_active_workspace(self):
        app = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("mcp-tool-handler", app)
        self.assertIn("agentufo_mcp/server.py", js)
        self.assertIn("agentufo_mcp/client_config.json", js)

    def test_normal_chat_fast_path_skips_expensive_logos_passes_by_default(self):
        handlers = (ROOT / "server" / "handlers.py").read_text(encoding="utf-8")
        self.assertIn("deep_mode = any(marker in lower_prompt", handlers)
        self.assertIn("4096 if deep_mode else 2048", handlers)
        self.assertIn("Only do a network search when the user explicitly requests fresh/current information", handlers)

    def test_runtime_persistence_and_telemetry_are_throttled(self):
        runtime = (ROOT / "server" / "crew_runtime.py").read_text(encoding="utf-8")
        self.assertIn("now_mono - self._last_persist_monotonic >= 1.5", runtime)
        self.assertIn('now_mono - telemetry_cache["at"] >= 0.8', runtime)


if __name__ == "__main__":
    unittest.main()
