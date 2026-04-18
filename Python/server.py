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
import usage  # noqa: E402
import scratch  # noqa: E402
import memory  # noqa: E402
import preferences as prefs_mod  # noqa: E402
import templates as template_lib  # noqa: E402
import wallet as wallet_mod  # noqa: E402
import project as project_mod  # noqa: E402
from skills import docx_fill, docx_grow  # noqa: E402
from skills import registry as tool_registry  # noqa: E402


def _urldecode(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


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
                "# steelg8 Soul\n\n- 方案不求人。\n- 回答直接，不铺垫。\n",
                encoding="utf-8",
            )

    return SOUL_PATH.read_text(encoding="utf-8").strip()


def soul_summary(soul_text: str) -> str:
    for line in soul_text.splitlines():
        line = line.strip()
        if line.startswith("- "):
            return line[2:]
    return "方案不求人。"


def build_system_prompt(
    soul_text: str,
    *,
    project_root: str | None = None,
    project_name: str = "",
) -> str:
    parts = [
        "## L1 · Soul",
        soul_text.strip(),
    ]

    mem_block = memory.compose_memory_block(
        include_user=True,
        project_root=project_root,
        project_name=project_name,
    )
    if mem_block:
        parts.append(mem_block)

    parts.append(
        "## 对话基调\n\n"
        "你是 steelg8 的本地内核。回答直接，根据当前请求挑合适的详略。"
        "遇到用户强调偏好 / 习惯 / 项目背景 / 重要决策时，可以用 remember() 工具"
        "把它记到 user.md 或 project/steelg8.md，之后的对话你会看到。"
    )
    return "\n\n".join(parts)


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

    active = project_mod.get_active()
    project_root = active.get("path") if active else None
    project_name = active.get("name", "") if active else ""

    return agent.AgentContext(
        system_prompt=build_system_prompt(
            soul_text,
            project_root=project_root,
            project_name=project_name,
        ),
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

        if self.path == "/usage/summary":
            self.respond(200, usage.summary())
            return

        if self.path == "/usage/recent":
            self.respond(200, {"items": usage.recent(limit=100)})
            return

        if self.path == "/scratch/note":
            self.respond(200, {"text": scratch.read()})
            return

        if self.path == "/templates":
            self.respond(200, {
                "dir": str(template_lib.default_dir()),
                "items": [t.to_dict() for t in template_lib.list_all()],
            })
            return

        if self.path == "/knowledge":
            import knowledge as knowledge_mod
            self.respond(200, {
                "dir": str(knowledge_mod.knowledge_root()),
                "items": knowledge_mod.list_cards(),
            })
            return

        if self.path == "/wallet":
            self.respond(200, wallet_mod.summary(self.registry))
            return

        if self.path == "/preferences":
            self.respond(200, prefs_mod.load())
            return

        if self.path == "/project":
            summary = project_mod.active_project_summary()
            self.respond(200, {"active": summary})
            return

        if self.path == "/project/status":
            self.respond(200, project_mod.status())
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

        if self.path == "/scratch/note":
            body = self.read_json() or {}
            text = body.get("text", "")
            if not isinstance(text, str):
                self.respond(400, {"error": "text must be string"})
                return
            scratch.write(text)
            self.respond(200, {"ok": True, "length": len(text)})
            return

        if self.path == "/project/open":
            self._handle_project_open()
            return

        if self.path == "/project/close":
            project_mod.close_project()
            self.respond(200, {"ok": True})
            return

        if self.path == "/project/reindex":
            self._handle_project_reindex()
            return

        if self.path == "/preferences":
            body = self.read_json() or {}
            if not isinstance(body, dict):
                self.respond(400, {"error": "invalid json"})
                return
            updated = prefs_mod.save(body)
            self.respond(200, updated)
            return

        if self.path == "/skills/docx/placeholders":
            self._handle_docx_placeholders()
            return
        if self.path == "/skills/docx/fill":
            self._handle_docx_fill()
            return
        if self.path == "/skills/docx/headings":
            self._handle_docx_headings()
            return
        if self.path == "/skills/docx/insert-section":
            self._handle_docx_insert_section()
            return
        if self.path == "/skills/docx/append-paragraphs":
            self._handle_docx_append_paragraphs()
            return
        if self.path == "/skills/docx/append-row":
            self._handle_docx_append_row()
            return

        self.respond(404, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.startswith("/templates/"):
            path = self.path[len("/templates/"):]
            path = _urldecode(path)
            ok = template_lib.delete(path)
            self.respond(200 if ok else 400, {"ok": ok})
            return
        self.respond(404, {"error": "not found"})

    # ------------------- handlers -------------------

    def _handle_project_open(self) -> None:
        body = self.read_json()
        if not isinstance(body, dict):
            self.respond(400, {"error": "invalid json"})
            return
        path = str(body.get("path", "")).strip()
        if not path:
            self.respond(400, {"error": "path is required"})
            return
        rebuild = bool(body.get("rebuild", True))
        try:
            proj = project_mod.open_project(path, self.registry, rebuild=rebuild)
        except ValueError as exc:
            self.respond(400, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            self.respond(500, {"error": f"{exc.__class__.__name__}: {exc}"})
            return

        self.respond(200, {
            "id": proj.id,
            "path": proj.path,
            "name": proj.name,
            "chunkCount": proj.chunk_count,
            "indexStatus": project_mod.status(),
        })

    def _handle_project_reindex(self) -> None:
        active = project_mod.get_active()
        if not active:
            self.respond(400, {"error": "没有激活的项目"})
            return
        try:
            proj = project_mod.open_project(
                active["path"], self.registry, rebuild=True
            )
        except Exception as exc:  # noqa: BLE001
            self.respond(500, {"error": f"{exc.__class__.__name__}: {exc}"})
            return
        self.respond(200, {
            "id": proj.id,
            "path": proj.path,
            "indexStatus": project_mod.status(),
        })

    # ------------------- docx skills -------------------

    def _handle_docx_placeholders(self) -> None:
        body = self.read_json() or {}
        path = str(body.get("path", "")).strip()
        if not path:
            self.respond(400, {"error": "path is required"})
            return
        try:
            names = docx_fill.list_placeholders(path)
        except docx_fill.DocxFillError as exc:
            self.respond(400, {"error": str(exc)})
            return
        self.respond(200, {"placeholders": names})

    def _handle_docx_fill(self) -> None:
        body = self.read_json() or {}
        template = str(body.get("template", "")).strip()
        data = body.get("data") or {}
        output = body.get("output") or None
        if not template:
            self.respond(400, {"error": "template is required"})
            return
        try:
            r = docx_fill.fill(template, data, output_path=output)
        except docx_fill.DocxFillError as exc:
            self.respond(400, {"error": str(exc)})
            return
        self.respond(200, {
            "output": r.output_path,
            "replaced": r.replaced_count,
            "missing": r.missing_keys,
            "leftover": r.leftover_placeholders,
        })

    def _handle_docx_headings(self) -> None:
        body = self.read_json() or {}
        path = str(body.get("path", "")).strip()
        if not path:
            self.respond(400, {"error": "path is required"})
            return
        try:
            hs = docx_grow.list_headings(path)
        except docx_grow.DocxGrowError as exc:
            self.respond(400, {"error": str(exc)})
            return
        self.respond(200, {"headings": hs})

    def _handle_docx_insert_section(self) -> None:
        body = self.read_json() or {}
        try:
            r = docx_grow.insert_section_after_heading(
                body["path"],
                after_heading=body["afterHeading"],
                new_heading=body["newHeading"],
                new_heading_level=int(body.get("newHeadingLevel", 2)),
                paragraphs=body.get("paragraphs") or [],
                anchor_level=body.get("anchorLevel"),
                output_path=body.get("output"),
            )
        except (KeyError, docx_grow.DocxGrowError) as exc:
            self.respond(400, {"error": str(exc)})
            return
        self.respond(200, {"output": r.output_path, "inserted": r.inserted_elements, "notes": r.notes})

    def _handle_docx_append_paragraphs(self) -> None:
        body = self.read_json() or {}
        try:
            r = docx_grow.append_paragraphs_after_heading(
                body["path"],
                after_heading=body["afterHeading"],
                paragraphs=body["paragraphs"],
                anchor_level=body.get("anchorLevel"),
                output_path=body.get("output"),
            )
        except (KeyError, docx_grow.DocxGrowError) as exc:
            self.respond(400, {"error": str(exc)})
            return
        self.respond(200, {"output": r.output_path, "inserted": r.inserted_elements})

    def _handle_docx_append_row(self) -> None:
        body = self.read_json() or {}
        try:
            r = docx_grow.append_table_row(
                body["path"],
                table_index=int(body.get("tableIndex", 0)),
                cells=body["cells"],
                output_path=body.get("output"),
            )
        except (KeyError, docx_grow.DocxGrowError) as exc:
            self.respond(400, {"error": str(exc)})
            return
        self.respond(200, {"output": r.output_path, "inserted": r.inserted_elements})

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

        # RAG：当前有激活项目且索引完成，就检索 top-K 注入 system prompt
        rag_hits = project_mod.retrieve(req.message, self.registry, top_k=5)
        if rag_hits:
            rag_block = "\n\n".join(
                f"[{i+1}] {h.rel_path} (score={h.score})\n{h.text}"
                for i, h in enumerate(rag_hits)
            )
            context.system_prompt = (
                context.system_prompt
                + "\n\n## 相关项目资料（按相似度 top-K，可引用）\n\n"
                + rag_block
            )

        decision = router.route(req.message, self.registry, explicit_model=req.model)
        provider: Provider | None = self.registry.providers.get(decision.provider) if decision.provider else None

        # Tools：目前只要用户能看到 docx skill 就挂上；token 开销 ~500，flash-lite
        # 跑一次 ¥0.001 级别，值得。后续如果要按对话意图切，在这里筛。
        tools = tool_registry.tool_schemas()
        registry_ref = self.registry
        tool_dispatch = lambda n, a: tool_registry.dispatch(n, a, registry=registry_ref)  # noqa: E731

        if stream:
            self._stream_response(
                req.message, context, provider, decision,
                rag_hits=rag_hits, tools=tools, tool_dispatch=tool_dispatch,
            )
        else:
            result = agent.run_once(
                req.message, context, provider, decision,
                tools=tools, tool_dispatch=tool_dispatch,
            )
            # 写 usage log（非 mock 且有 usage 的时候记一条；mock 不计费）
            if result.source.startswith("provider:") and result.usage:
                usage.record(
                    model=result.decision.model,
                    provider=result.decision.provider,
                    layer=result.decision.layer,
                    prompt_tokens=result.usage.get("prompt_tokens", 0),
                    completion_tokens=result.usage.get("completion_tokens", 0),
                )
            payload = result.to_dict()
            payload["soulSummary"] = soul_summary(soul_text)
            if rag_hits:
                payload["ragHits"] = [
                    {"relPath": h.rel_path, "chunkIdx": h.chunk_idx,
                     "score": h.score, "preview": h.text[:240]}
                    for h in rag_hits
                ]
            self.respond(200, payload)

    def _stream_response(
        self,
        message: str,
        context: agent.AgentContext,
        provider: Provider | None,
        decision: router.RoutingDecision,
        *,
        rag_hits: list | None = None,
        tools: list | None = None,
        tool_dispatch: Any = None,
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

        # 先把 rag_hits 作为专门事件发出去（meta 事件之前前端就能挂 UI）
        if rag_hits:
            try:
                write_event({
                    "type": "rag",
                    "hits": [
                        {"relPath": h.rel_path, "chunkIdx": h.chunk_idx,
                         "score": h.score, "preview": h.text[:240]}
                        for h in rag_hits
                    ],
                })
            except (BrokenPipeError, ConnectionResetError):
                return

        captured_usage: dict[str, int] | None = None
        captured_model: str | None = None
        try:
            for event in agent.run_stream(
                message, context, provider, decision,
                tools=tools, tool_dispatch=tool_dispatch,
            ):
                # 捕获 usage 事件用于写 log（event 本身正常下发给前端）
                if event.get("type") == "usage":
                    captured_usage = event.get("usage")
                    captured_model = event.get("model")
                write_event(event)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端断开，不是错误
            return

        # 流结束后写 usage log（mock 走 layer==mock，不记账）
        if (
            provider is not None
            and decision.layer != "mock"
            and captured_usage
            and (captured_usage.get("prompt_tokens") or captured_usage.get("completion_tokens"))
        ):
            usage.record(
                model=captured_model or decision.model,
                provider=decision.provider,
                layer=decision.layer,
                prompt_tokens=captured_usage.get("prompt_tokens", 0),
                completion_tokens=captured_usage.get("completion_tokens", 0),
            )

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
