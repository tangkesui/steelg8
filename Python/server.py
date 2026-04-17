#!/usr/bin/env python3
"""
steelg8 Phase 0 · 本地最小内核

职责：
- 维护 soul.md（人格底座，L1 记忆层）
- 暴露 HTTP 接口：/health /chat /providers
- 通过 provider registry 把 /chat 请求路由到 Kimi / DeepSeek / Qwen / OpenRouter
- 无任何第三方依赖，stdlib only（为了安装门槛）

后续（Phase 1+）会换成 Fork 自 Hermes 的完整 agent loop，届时本文件会降级为"冷启动
引导器"，业务逻辑迁移到独立的 agent 模块。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import request

# 把自身目录加入 sys.path，方便作为脚本直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent))

from providers import load_registry, Provider, ProviderRegistry  # noqa: E402


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
            "你是 steelg8 的本地最小内核。回答直接，优先确认链路是否打通，再给一句像样的启动反馈。",
        ]
    ).strip()


@dataclass
class ChatResult:
    content: str
    model: str
    source: str
    soul_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "source": self.source,
            "soulSummary": self.soul_summary,
        }


def _call_provider(
    provider: Provider,
    model: str,
    message: str,
    soul_text: str,
) -> ChatResult:
    payload = {
        "model": model or (provider.models[0] if provider.models else ""),
        "messages": [
            {"role": "system", "content": build_system_prompt(soul_text)},
            {"role": "user", "content": message},
        ],
        "temperature": 0.4,
    }

    req = request.Request(
        f"{provider.base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {provider.api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    content = body["choices"][0]["message"]["content"].strip()
    resolved_model = body.get("model") or payload["model"]

    return ChatResult(
        content=content or "上游模型返回了空响应。",
        model=resolved_model,
        source=f"provider:{provider.name}",
        soul_summary=soul_summary(soul_text),
    )


def mock_reply(
    message: str,
    model: str | None,
    soul_text: str,
    *,
    error_note: str | None = None,
    reason: str | None = None,
) -> ChatResult:
    summary = soul_summary(soul_text)
    pieces = [
        "steelg8 Phase 0 已接通。",
        f"本地内核收到了：{message}",
        f"当前人格底色：{summary}",
    ]
    if reason:
        pieces.append(f"触发 mock 的原因：{reason}。")
    if error_note:
        pieces.append(f"上游调用失败，已自动降级：{error_note}")
    pieces.append(
        "把 ~/.steelg8/providers.json 配好任意一家真 provider（Kimi/Qwen/DeepSeek/OpenRouter），就能切到真模型。"
    )

    return ChatResult(
        content=" ".join(pieces),
        model=model or "mock-local",
        source="mock-fallback",
        soul_summary=summary,
    )


def handle_chat(
    message: str,
    requested_model: str | None,
    registry: ProviderRegistry,
) -> ChatResult:
    soul_text = ensure_soul_file()

    # 1. 指定了 model：按 model 找 provider
    if requested_model:
        resolved = registry.resolve(requested_model)
        if resolved:
            provider, model = resolved
            try:
                return _call_provider(provider, model, message, soul_text)
            except Exception as exc:  # noqa: BLE001
                return mock_reply(
                    message,
                    requested_model,
                    soul_text,
                    error_note=str(exc),
                )
        # 指定模型但未命中任何 provider → 尝试默认
        return mock_reply(
            message,
            requested_model,
            soul_text,
            reason=f"provider registry 没有 '{requested_model}' 对应的 provider 或 provider 未就绪",
        )

    # 2. 没指定 model：用 default_model
    if registry.default_model:
        resolved = registry.resolve(registry.default_model)
        if resolved:
            provider, model = resolved
            try:
                return _call_provider(provider, model, message, soul_text)
            except Exception as exc:  # noqa: BLE001
                return mock_reply(
                    message,
                    registry.default_model,
                    soul_text,
                    error_note=str(exc),
                )

    # 3. 默认也不可用：找第一个 ready 的 provider 兜底
    ready = registry.first_ready()
    if ready:
        provider, model = ready
        try:
            return _call_provider(provider, model, message, soul_text)
        except Exception as exc:  # noqa: BLE001
            return mock_reply(
                message,
                model,
                soul_text,
                error_note=str(exc),
            )

    # 4. 全部未就绪：mock
    return mock_reply(
        message,
        None,
        soul_text,
        reason="没有任何 provider 处于就绪状态（base_url + api_key 缺失）",
    )


class SteelG8Handler(BaseHTTPRequestHandler):
    server_version = "steelg8/0.1"
    registry: ProviderRegistry  # 由 main 注入

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

        self.respond(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/chat":
            self.respond(404, {"error": "not found"})
            return

        body = self.read_json()
        if not isinstance(body, dict):
            self.respond(400, {"error": "invalid json"})
            return

        message = str(body.get("message", "")).strip()
        requested_model = body.get("model")

        if not message:
            self.respond(400, {"error": "message is required"})
            return

        result = handle_chat(message, requested_model, self.registry)
        self.respond(200, result.to_dict())

    def read_json(self) -> Any:
        raw_length = self.headers.get("Content-Length", "0")
        length = int(raw_length) if raw_length.isdigit() else 0
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

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
    parser = argparse.ArgumentParser(description="steelg8 Phase 0 local server")
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
