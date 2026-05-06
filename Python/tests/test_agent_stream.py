"""
agent.run_stream 的状态机回归测试。

不打真上游。通过 patch `agent._post_sse` 注入预设的 chunk 序列，
验证：
- 单轮纯文本：delta → usage → done。
- tool_calls delta 多 chunk 合并：name / arguments 跨 chunk，工具被
  实际 dispatch，tool_start / tool_result / _transcript 顺序正确。
- 上游中断：error 事件 + mock 降级 done 仍有。
- thinking 模型：reasoning_delta 累计，回放消息携带 reasoning_content；
  即便没流出 reasoning，也补空串兜底。
- 多轮 usage 跨 iter 聚合：prompt/completion/total 累加。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402
from providers import Provider  # noqa: E402
from router import RoutingDecision  # noqa: E402


def _make_provider() -> Provider:
    p = Provider(
        name="kimi",
        base_url="https://example.test/v1",
        api_key_inline="test-key",
        models=["kimi-k2.5"],
    )
    return p


def _decision(model: str = "kimi-k2.5") -> RoutingDecision:
    return RoutingDecision(model=model, provider="kimi", layer="explicit", reason="test")


def _drive(events_iter):
    """收集 run_stream 的所有事件。"""
    return list(events_iter)


class _FakeSSE:
    """构造一个可重复调用的假 _post_sse —— 每次 iter 用一组预设 chunk。"""

    def __init__(self, batches: list[list[dict]]):
        self.batches = list(batches)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if not self.batches:
            raise AssertionError("FakeSSE 被调用次数超过预期")
        chunks = self.batches.pop(0)
        for chunk in chunks:
            yield chunk


class AgentStreamTests(unittest.TestCase):
    def test_single_turn_text_flow_emits_delta_usage_done(self):
        provider = _make_provider()
        decision = _decision(model="not-thinking-model")
        ctx = agent.AgentContext(system_prompt="你好")
        fake_sse = _FakeSSE([
            [
                {"model": "not-thinking-model", "delta": "Hi"},
                {"delta": " there"},
                {"usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}},
            ]
        ])
        with patch.object(agent, "_post_sse", fake_sse):
            events = _drive(agent.run_stream("ping", ctx, provider, decision))

        types = [e["type"] for e in events]
        self.assertEqual(types[0], "meta")
        self.assertIn("delta", types)
        self.assertIn("usage", types)
        self.assertEqual(types[-1], "done")

        full = next(e for e in events if e["type"] == "done")["full"]
        self.assertEqual(full, "Hi there")

        usage_event = next(e for e in events if e["type"] == "usage")
        self.assertEqual(usage_event["usage"]["prompt_tokens"], 11)
        self.assertEqual(usage_event["usage"]["completion_tokens"], 7)
        self.assertEqual(usage_event["usage"]["total_tokens"], 18)

    def test_tool_calls_delta_merge_dispatches_and_emits_transcript(self):
        provider = _make_provider()
        decision = _decision(model="not-thinking-model")
        ctx = agent.AgentContext()
        fake_sse = _FakeSSE([
            # 第一轮：tool_calls delta 跨多个 chunk（name 一段、arguments 两段）
            [
                {"tool_calls_delta": [
                    {"index": 0, "id": "call_1", "type": "function",
                     "function": {"name": "remember"}}
                ]},
                {"tool_calls_delta": [
                    {"index": 0, "function": {"arguments": "{\"layer\":"}}
                ]},
                {"tool_calls_delta": [
                    {"index": 0, "function": {"arguments": "\"user\",\"text\":\"x\"}"}}
                ]},
                {"usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}},
            ],
            # 第二轮：在 tool result 之后产出最终文本
            [
                {"delta": "ok"},
                {"usage": {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8}},
            ],
        ])

        dispatched: list[tuple[str, dict]] = []

        def fake_dispatch(name, args):
            dispatched.append((name, args))
            return {"ok": True}

        with patch.object(agent, "_post_sse", fake_sse):
            events = _drive(
                agent.run_stream("hi", ctx, provider, decision, tool_dispatch=fake_dispatch)
            )

        # tool_dispatch 必须收到合并后的 args
        self.assertEqual(dispatched, [("remember", {"layer": "user", "text": "x"})])

        # 事件序列必须含 tool_start → tool_result，且二者 id 一致
        tool_events = [e for e in events if e["type"] in {"tool_start", "tool_result"}]
        self.assertEqual([e["type"] for e in tool_events], ["tool_start", "tool_result"])
        self.assertEqual(tool_events[0]["id"], tool_events[1]["id"])
        self.assertEqual(tool_events[0]["name"], "remember")

        # _transcript 出现两次：assistant(tool_calls) 和 role=tool
        transcripts = [e["message"] for e in events if e["type"] == "_transcript"]
        self.assertEqual(transcripts[0]["role"], "assistant")
        self.assertEqual(transcripts[0]["tool_calls"][0]["function"]["name"], "remember")
        self.assertEqual(transcripts[1]["role"], "tool")
        self.assertEqual(transcripts[1]["tool_call_id"], "call_1")

        # usage 跨两轮聚合
        usage = next(e for e in events if e["type"] == "usage")["usage"]
        self.assertEqual(usage["prompt_tokens"], 11)
        self.assertEqual(usage["completion_tokens"], 5)
        self.assertEqual(usage["total_tokens"], 16)

        # done 的 full 来自第二轮
        self.assertEqual(next(e for e in events if e["type"] == "done")["full"], "ok")

    def test_upstream_exception_falls_back_to_mock_done(self):
        provider = _make_provider()
        decision = _decision(model="not-thinking-model")
        ctx = agent.AgentContext()

        def boom_sse(*args, **kwargs):
            yield {"delta": "partial"}
            raise RuntimeError("network exploded")

        with patch.object(agent, "_post_sse", boom_sse):
            events = _drive(agent.run_stream("hi", ctx, provider, decision))

        types = [e["type"] for e in events]
        self.assertIn("error", types)
        self.assertEqual(types[-1], "done")
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["source"], "mock-fallback")
        # mock 降级仍把上游已流出的内容拼进 full
        self.assertIn("partial", done["full"])

    def test_thinking_model_reasoning_content_round_trips(self):
        provider = _make_provider()
        decision = _decision(model="kimi-k2-thinking")
        ctx = agent.AgentContext()
        fake_sse = _FakeSSE([
            # 第一轮：reasoning_delta 跨两个 chunk + 一个 tool_call delta
            [
                {"reasoning_delta": "想"},
                {"reasoning_delta": "一下"},
                {"tool_calls_delta": [
                    {"index": 0, "id": "call_t", "type": "function",
                     "function": {"name": "remember", "arguments": "{}"}}
                ]},
            ],
            # 第二轮：没有 reasoning_delta —— thinking 模型也应补空串兜底
            [
                {"delta": "done"},
            ],
        ])

        with patch.object(agent, "_post_sse", fake_sse):
            events = _drive(
                agent.run_stream("hi", ctx, provider, decision, tool_dispatch=lambda n, a: {"ok": True})
            )

        transcripts = [e["message"] for e in events if e["type"] == "_transcript"]
        # 第一条 assistant 必须带累计后的 reasoning_content
        self.assertEqual(transcripts[0]["role"], "assistant")
        self.assertEqual(transcripts[0]["reasoning_content"], "想一下")

    def test_thinking_model_empty_reasoning_still_attaches_field(self):
        provider = _make_provider()
        decision = _decision(model="kimi-k2-thinking")
        ctx = agent.AgentContext()
        fake_sse = _FakeSSE([
            [
                {"tool_calls_delta": [
                    {"index": 0, "id": "call_x", "type": "function",
                     "function": {"name": "remember", "arguments": "{}"}}
                ]},
            ],
            [
                {"delta": "ok"},
            ],
        ])

        with patch.object(agent, "_post_sse", fake_sse):
            events = _drive(
                agent.run_stream("hi", ctx, provider, decision, tool_dispatch=lambda n, a: {"ok": True})
            )

        transcripts = [e["message"] for e in events if e["type"] == "_transcript"]
        assistant = transcripts[0]
        self.assertIn("reasoning_content", assistant)
        self.assertEqual(assistant["reasoning_content"], "")


if __name__ == "__main__":
    unittest.main()
