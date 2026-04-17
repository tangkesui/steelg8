#!/usr/bin/env python3
"""
steelg8 Phase 0.5 · 本地最小内核

职责：
- 维护 soul.md（人格底座，L1 记忆层）
- 暴露 HTTP 接口：/health /chat /chat/stream /providers /providers/reload
- 把 /chat 请求交给 router.route() 做四层漏斗决策，再由 agent.run_* 跑
- 无任何第三方依赖，stdlib only

相对 Phase 0 的变化：
- 引入 router.py（四层漏斗 MVP 版）
- 引入 agent.py（最小 agent loop + 流式）
- 新增 /chat/stream：SSE，用于 WebView 流式渲染
- /chat 的返回结构里加 routingLayer / routingReason 方便前端 UI 显示
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from providers import load_registry, Provider, ProviderRegistry  # noqa: E402
import router  # noqa: E402
import agent  # noqa: E402


def app_root() -> Path:
    env_root = os.environ.get("STEELG8_APP_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


APP_ROOT = app_root()
DEFAULT_CONFIG_DIR = Path.home() / ".steelg8"
SOUL_PATH = Path(os.environ.get("STEELG8_SOUL_PATH", DEFAULT_CONFIG_DIR / "soul.md")).expanduser()
SOUL_TEMPLATE_PATH = APP_ROOT / "prompts" / "soul.md"
EXAMPLE_PROVIDERS_PATH = APP_ROOT / "config" / "providers.example.json"


def ensure_soul_file() -> str:
    SOUL_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not SOUL_PATH.exists():
        if SOUL_TEMPLATE_PATH.exists():
            SOUL_PATH.write_text(SOUL_TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            SOUL_PATH.write_text(
                "# steelg8 Soul\n\n- 让文案工作者不用再求任何人。\n- 回答直接，不铺垫。\n",
                encoding="utf-8",
            )

    return SOUL_PATH.read_text(encoding="utf-8").strip()


def soul_summary(soul_text: str) -> str:
    for line in soul_text.splitlines():
        line = line.strip()
        if line.startswith("- "):
            return line[2:]
    return "让文案工作者不用再求任何人。"


def build_system_prompt(soul_text: str) -> str:
    return "\n\n".join(
        [
            soul_text.strip(),
            "你是 steelg8 的本地内核。回答直接，根据当前请求挑合适的详略，必要时先确认链路打通。",
        ]
    ).strip()


@dataclass
class ChatRequest:
    message: str
    model: str | None
    history: list[dict[str, Any]]
    stream: bool

    @classmethod
    def parse(cls, body: Any, *, stream_endpoint: bool) -> "ChatRequest | None":
        if not isinstance(body, dict):
            return None
        message = str(body.get("message", "")).strip()
        if not message:
            return None
        model = body.get("model") or None
        history = body.get("history") or []
        if not isinstance(history, list):
            history = []
        return cls(
            message=message,
            model=model,
            history=history,
            stream=bool(body.get("stream")) or stream_endpoint,
        )


def _context_from_request(req: ChatRequest, soul_text: str) -> agent.AgentContext:
    msgs: list[agent.ChatMessage] = []
    for entry in req.history:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", "")).strip()
        content = str(entry.get("content", "")).strip()
        if role in {"user", "assistant", "system", "tool"} and content:
            msgs.append(agent.ChatMessage(role=role, content=content))

    return agent.AgentContext(
        system_prompt=build_system_prompt(soul_text),
        history=msgs,
    )


class SteelG8Handler(BaseHTTPRequestHandler):
    server_version = "steelg8/0.2"
    registry: ProviderRegistry  # 由 main 注入

    # --- CORS 永久开启：本地内核只绑 127.0.0.1，允许 file:// / localhost
    # 所有源调用是工程必需（WKWebView 加载 file://、浏览器调试）
    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Accept")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            soul_text = ensure_soul_file()
            self.respond(
                200,
                {
                    "ok": True,
                    "mode": self._mode_label(),
                    "defaultModel": self.registry.default_model,
                    "soulSummary": soul_summary(soul_text),
                    "appRoot": str(APP_ROOT),
                    "providerSource": self.registry.source,
                },
            )
            return

        if self.path == "/providers":
            self.respond(
                200,
                {
                    "source": self.registry.source,
                    "defaultModel": self.registry.default_model,
                    "providers": self.registry.readiness_summary(),
                },
            )
            return

        if self.path == "/capabilities":
            # 画像表快照，前端可用来展示模型能力
            import capabilities as caps
            self.respond(
                200,
                {
                    "profiles": [
                        {
                            "model": p.model,
                            "provider": p.provider,
                            "chineseWriting": p.chinese_writing,
                            "englishWriting": p.english_writing,
                            "reasoning": p.reasoning,
                            "contextTokens": p.context_tokens,
                            "costTier": p.cost_tier,
                            "latencyTier": p.latency_tier,
                            "toolUse": p.tool_use,
                            "tags": list(p.tags),
                        }
                        for p in caps.all_profiles()
                    ]
                },
            )
            return

        self.respond(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/providers/reload":
            self._handle_reload()
            return

        if self.path == "/chat":
            self._handle_chat(stream=False)
            return

        if self.path == "/chat/stream":
            self._handle_chat(stream=True)
            return

        self.respond(404, {"error": "not found"})

    # ------------------- handlers -------------------

    def _handle_reload(self) -> None:
        new_registry = load_registry(example_candidates=(EXAMPLE_PROVIDERS_PATH,))
        if os.environ.get("STEELG8_DEFAULT_MODEL"):
            new_registry.default_model = os.environ["STEELG8_DEFAULT_MODEL"]
        type(self).registry = new_registry
        self.respond(
            200,
            {
                "ok": True,
                "source": new_registry.source,
                "defaultModel": new_registry.default_model,
                "providers": new_registry.readiness_summary(),
                "readyProviders": [
                    p.name for p in new_registry.providers.values() if p.is_ready()
                ],
            },
        )

    def _handle_chat(self, *, stream: bool) -> None:
        body = self.read_json()
        req = ChatRequest.parse(body, stream_endpoint=stream)
        if req is None:
            self.respond(400, {"error": "message is required"})
            return

        soul_text = ensure_soul_file()
        context = _context_from_request(req, soul_text)

        decision = router.route(req.message, self.registry, explicit_model=req.model)
        provider: Provider | None = self.registry.providers.get(decision.provider) if decision.provider else None

        if stream:
            self._stream_response(req.message, context, provider, decision)
        else:
            result = agent.run_once(req.message, context, provider, decision)
            payload = result.to_dict()
            payload["soulSummary"] = soul_summary(soul_text)
            self.respond(200, payload)

    def _stream_response(
        self,
        message: str,
        context: agent.AgentContext,
        provider: Provider | None,
        decision: router.RoutingDecision,
    ) -> None:
        # SSE 头
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def write_event(event: dict[str, Any]) -> None:
            line = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            try:
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                raise

        try:
            for event in agent.run_stream(message, context, provider, decision):
                write_event(event)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端断开，不是错误
            return

    # ------------------- io utilities -------------------

    def read_json(self) -> Any:
        raw_length = self.headers.get("Content-Length", "0")
        length = int(raw_length) if raw_length.isdigit() else 0
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"steelg8 http: {format % args}\n")

    def _mode_label(self) -> str:
        if self.registry.first_ready() is not None:
            return f"provider-registry ({self.registry.source})"
        return "mock-fallback"


def main() -> None:
    parser = argparse.ArgumentParser(description="steelg8 Phase 0.5 local server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("STEELG8_PORT", "8765")))
    args = parser.parse_args()

    ensure_soul_file()

    registry = load_registry(example_candidates=(EXAMPLE_PROVIDERS_PATH,))
    if os.environ.get("STEELG8_DEFAULT_MODEL"):
        registry.default_model = os.environ["STEELG8_DEFAULT_MODEL"]

    SteelG8Handler.registry = registry

    server = ThreadingHTTPServer(("127.0.0.1", args.port), SteelG8Handler)
    print(
        json.dumps(
            {
                "event": "server_started",
                "port": args.port,
                "providerSource": registry.source,
                "defaultModel": registry.default_model,
                "readyProviders": [p.name for p in registry.providers.values() if p.is_ready()],
                "soulPath": str(SOUL_PATH),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
