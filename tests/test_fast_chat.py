from __future__ import annotations
import unittest
from types import SimpleNamespace

from server.handlers import ChatSessionManager


class FastChatTests(unittest.TestCase):
    def test_normal_conversation_uses_one_bounded_model_call(self):
        manager = ChatSessionManager.__new__(ChatSessionManager)
        manager.jev_engine = SimpleNamespace(evaluate=lambda prompt: SimpleNamespace(
            is_safe=SimpleNamespace(decision=True),
            requires_sandbox=SimpleNamespace(decision=False),
            intent=SimpleNamespace(value="general"),
            to_dict=lambda: {"safe": True},
        ))
        calls = []
        class FakeLLM:
            provider_name = "openai"
            model_name = "test-model"
            def generate(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(content="Hello there.", total_tokens=5)
        class ForbiddenHeavyLayer:
            def __getattr__(self, name):
                raise AssertionError("Normal chat should not invoke deep LOGOS layers")
        manager.operator = SimpleNamespace(
            llm=FakeLLM(),
            _safe_llm_max_tokens=lambda cap: min(cap, 2048),
            pondering=ForbiddenHeavyLayer(), nas=ForbiddenHeavyLayer(), decomposer=ForbiddenHeavyLayer(),
            holographic_mem=ForbiddenHeavyLayer(),
        )
        manager.temperature = 0.2
        manager.provider_name = "openai"
        manager.model_name = "test-model"
        manager.history = []
        manager.current_chat_id = None
        manager.saved_chats = {}
        result = manager.process_chat("Hi, how are you?")
        self.assertEqual(result["content"], "Hello there.")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_tokens"], 2048)
        self.assertEqual(result["thinking"]["mode"], "fast")

    def test_history_retention_is_bounded(self):
        manager = ChatSessionManager.__new__(ChatSessionManager)
        manager.history = []
        for i in range(75):
            manager._append_history(f"prompt {i}", {"content": "x" * 20000, "thinking": {"audit_history": ["large"]}})
        self.assertEqual(len(manager.history), 60)
        self.assertLessEqual(len(manager.history[-1]["result"]["content"]), 10050)
        self.assertNotIn("audit_history", manager.history[-1]["result"]["thinking"])


if __name__ == "__main__":
    unittest.main()
