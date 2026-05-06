from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402
import conversations as conv_store  # noqa: E402
import history_manager  # noqa: E402
import router  # noqa: E402
from services import chat_persistence  # noqa: E402
from services import chat_service  # noqa: E402


class TempConversationDB:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = conv_store.DB_PATH
        self.old_inited = conv_store._INITED
        conv_store.DB_PATH = Path(self.tmp.name) / "conversations.db"
        conv_store._INITED = False
        return self

    def __exit__(self, exc_type, exc, tb):
        conv_store.DB_PATH = self.old_path
        conv_store._INITED = self.old_inited
        self.tmp.cleanup()


def tool_call(call_id: str = "call_1", name: str = "remember") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": "{\"text\":\"hello\"}"},
    }


class HistorySanitizerTests(unittest.TestCase):
    def test_sanitize_drops_orphan_tool_messages(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "orphan", "content": "{}"},
            {"role": "assistant", "content": "ok"},
        ]
        sanitized = history_manager._sanitize_openai_history(messages)
        self.assertEqual([m["role"] for m in sanitized], ["user", "assistant"])

    def test_sanitize_keeps_only_expected_tool_results(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [tool_call("call_a"), tool_call("call_b")],
            },
            {"role": "tool", "tool_call_id": "extra", "content": "{\"bad\":true}"},
            {"role": "tool", "tool_call_id": "call_b", "content": "{\"b\":true}"},
            {"role": "tool", "tool_call_id": "call_a", "content": "{\"a\":true}"},
            {"role": "assistant", "content": "done"},
        ]
        sanitized = history_manager._sanitize_openai_history(messages)
        self.assertEqual(len(sanitized), 4)
        self.assertEqual(sanitized[0]["role"], "assistant")
        self.assertEqual([m.get("tool_call_id") for m in sanitized[1:3]], ["call_a", "call_b"])
        self.assertEqual(sanitized[3]["content"], "done")

    def test_sanitize_downgrades_incomplete_assistant_tool_call_when_it_has_text(self):
        messages = [
            {
                "role": "assistant",
                "content": "I will check that.",
                "tool_calls": [tool_call("missing")],
            },
            {"role": "assistant", "content": "continue"},
        ]
        sanitized = history_manager._sanitize_openai_history(messages)
        self.assertEqual(sanitized[0], {"role": "assistant", "content": "I will check that."})
        self.assertEqual(sanitized[1]["content"], "continue")


class CompressionBoundaryTests(unittest.TestCase):
    def test_safe_compression_boundary_keeps_tool_group_in_tail(self):
        msgs = [
            conv_store.StoredMessage(i, 1, "user", f"u{i}", None, [], None, 1, False, i)
            for i in range(1, 8)
        ]
        msgs.extend([
            conv_store.StoredMessage(8, 1, "assistant", "", None, [tool_call("call_a")], None, 1, False, 8),
            conv_store.StoredMessage(9, 1, "tool", "{}", None, [], "call_a", 1, False, 9),
            conv_store.StoredMessage(10, 1, "assistant", "done", None, [], None, 1, False, 10),
        ])
        keep_start = history_manager._safe_compression_keep_start(msgs, keep_tail_messages=2)
        self.assertEqual(keep_start, 7)
        self.assertEqual([m.role for m in msgs[keep_start:]], ["assistant", "tool", "assistant"])

    def test_maybe_compress_leaves_complete_tool_group_active(self):
        with TempConversationDB():
            conv = conv_store.create_conversation(title="test")
            for idx in range(7):
                conv_store.append_message(conv.id, role="user", content=f"older {idx}", tokens=20)
            conv_store.append_message(
                conv.id,
                role="assistant",
                content="",
                tool_calls=[tool_call("call_tail")],
                tokens=20,
            )
            conv_store.append_message(
                conv.id,
                role="tool",
                content="{\"ok\":true}",
                tool_call_id="call_tail",
                tokens=20,
            )
            conv_store.append_message(conv.id, role="assistant", content="tail answer", tokens=20)

            with patch.object(history_manager, "KEEP_TAIL_MESSAGES", 2), \
                 patch.object(
                     history_manager,
                     "model_budget",
                     return_value=history_manager.BudgetPlan(
                         model="test",
                         context_tokens=100,
                         reserved=0,
                         budget_for_history=100,
                         trigger_tokens=1,
                     ),
                 ), \
                 patch.object(history_manager, "_call_qwen_turbo", return_value=None):
                result = history_manager.maybe_compress(
                    conv.id,
                    Mock(),
                    model="test",
                    system_prompt_tokens=0,
                )
            active = conv_store.list_messages(conv.id, only_active=True)

        self.assertTrue(result.compressed)
        self.assertEqual(result.compressed_count, 7)
        self.assertEqual([m.role for m in active], ["assistant", "tool", "assistant"])
        self.assertEqual(active[0].tool_calls[0]["id"], "call_tail")
        self.assertEqual(active[1].tool_call_id, "call_tail")


class TranscriptPersistenceTests(unittest.TestCase):
    def test_persist_transcript_messages_round_trips_complete_tool_group(self):
        with TempConversationDB():
            conv = conv_store.create_conversation(title="test")
            chat_persistence.persist_transcript_messages(
                conv.id,
                [
                    {"role": "assistant", "content": "", "tool_calls": [tool_call("call_a")]},
                    {"role": "tool", "tool_call_id": "call_a", "content": "{\"ok\":true}"},
                ],
            )
            history = history_manager.build_history_for_llm(conv.id)
        self.assertEqual([m["role"] for m in history], ["assistant", "tool"])
        self.assertEqual(history[0]["tool_calls"][0]["id"], "call_a")
        self.assertEqual(history[1]["tool_call_id"], "call_a")

    def test_build_history_for_llm_drops_orphan_tool_rows_from_db(self):
        with TempConversationDB():
            conv = conv_store.create_conversation(title="test")
            conv_store.append_message(conv.id, role="user", content="hi")
            conv_store.append_message(
                conv.id,
                role="tool",
                content="{\"orphan\":true}",
                tool_call_id="missing",
            )
            conv_store.append_message(conv.id, role="assistant", content="safe")
            history = history_manager.build_history_for_llm(conv.id)
        self.assertEqual([m["role"] for m in history], ["user", "assistant"])
        self.assertEqual(history[1]["content"], "safe")

    def test_run_once_persists_tool_transcript_before_final_assistant(self):
        decision = router.RoutingDecision(
            model="mock-model",
            provider="mock-provider",
            layer="explicit",
            reason="test",
        )
        result = agent.AgentResult(
            content="final answer",
            decision=decision,
            source="provider:mock",
            usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            transcript_messages=[
                {"role": "assistant", "content": "", "tool_calls": [tool_call("call_a")]},
                {"role": "tool", "tool_call_id": "call_a", "content": "{\"ok\":true}"},
            ],
        )
        with TempConversationDB():
            conv = conv_store.create_conversation(title="test")
            prepared = Mock(
                conversation_id=conv.id,
                request=Mock(message="hi"),
                context=Mock(),
                provider=Mock(),
                decision=decision,
                tools=[],
                tool_dispatch=lambda *_: None,
                soul_text="- soul",
                compression_result=history_manager.CompressionResult(False),
                rag_hits=[],
            )
            with patch.object(chat_service.agent, "run_once", return_value=result), \
                 patch.object(chat_persistence.usage, "record"):
                chat_service.run_once(prepared)
            messages = conv_store.list_messages(conv.id, only_active=False)
        self.assertEqual([m.role for m in messages], ["assistant", "tool", "assistant"])
        self.assertEqual(messages[0].tool_calls[0]["id"], "call_a")
        self.assertEqual(messages[1].tool_call_id, "call_a")
        self.assertEqual(messages[2].content, "final answer")

    def test_persist_stream_final_uses_same_transcript_shape(self):
        with TempConversationDB():
            conv = conv_store.create_conversation(title="test")
            prepared = Mock(conversation_id=conv.id)
            chat_persistence.persist_stream_final(
                prepared,
                transcript=[
                    {"role": "assistant", "content": "", "tool_calls": [tool_call("call_a")]},
                    {"role": "tool", "tool_call_id": "call_a", "content": "{\"ok\":true}"},
                ],
                content="stream final",
                usage_payload={"completion_tokens": 2},
            )
            messages = conv_store.list_messages(conv.id, only_active=False)
        self.assertEqual([m.role for m in messages], ["assistant", "tool", "assistant"])
        self.assertEqual(messages[2].tokens, 2)


class ChatPreparationTests(unittest.TestCase):
    def test_context_probe_isolates_history_and_rag(self):
        decision = router.RoutingDecision(
            model="mock-local",
            provider="",
            layer="mock",
            reason="test",
        )
        with TempConversationDB():
            conv = conv_store.create_conversation(title="test")
            conv_store.append_message(conv.id, role="user", content="继续完成 V1.5 文档")
            conv_store.append_message(conv.id, role="assistant", content="我继续处理 V1.5")

            with patch.object(chat_service.router, "route", return_value=decision), \
                 patch.object(chat_service.project_mod, "retrieve") as retrieve:
                prepared = chat_service.prepare_chat(
                    {"message": "ping", "conversationId": conv.id},
                    Mock(providers={}),
                    soul_text="- soul",
                    stream_endpoint=True,
                )

            self.assertEqual(prepared.context.history_dicts, [])
            self.assertEqual(prepared.rag_hits, [])
            retrieve.assert_not_called()
            messages = prepared.context.build_messages("ping")
            self.assertEqual(messages[-1], {"role": "user", "content": "ping"})
            self.assertIn("上下文隔离", messages[0]["content"])

    def test_regular_message_keeps_history_and_rag(self):
        decision = router.RoutingDecision(
            model="mock-local",
            provider="",
            layer="mock",
            reason="test",
        )
        with TempConversationDB():
            conv = conv_store.create_conversation(title="test")
            conv_store.append_message(conv.id, role="user", content="项目背景")

            with patch.object(chat_service.router, "route", return_value=decision), \
                 patch.object(chat_service.project_mod, "retrieve", return_value=[]) as retrieve:
                prepared = chat_service.prepare_chat(
                    {"message": "继续写方案", "conversationId": conv.id},
                    Mock(providers={}),
                    soul_text="- soul",
                    stream_endpoint=True,
                )

            self.assertEqual(prepared.context.history_dicts[0]["content"], "项目背景")
            retrieve.assert_called_once()


if __name__ == "__main__":
    unittest.main()
