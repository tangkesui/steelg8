#!/usr/bin/env python3
"""
steelg8 Phase 0.5 · 本地最小内核

职责：
- 维护 soul.md（人格底座，L1 记忆层）
- 暴露 HTTP 接口：/health /chat /chat/stream /providers /providers/reload
- 把 /chat 请求交给 router.route() 做显式 / 默认 / 兜底 / mock 路由，再由 agent.run_* 跑
- 无任何第三方依赖，stdlib only

相对 Phase 0 的变化：
- 引入 router.py（简化模型路由 MVP 版）
- 引入 agent.py（最小 agent loop + 流式）
- 新增 /chat/stream：SSE，用于 WebView 流式渲染
- /chat 的返回结构里加 routingLayer / routingReason 方便前端 UI 显示
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_migrate  # noqa: E402
import logger  # noqa: E402
from kernel import request as http_request  # noqa: E402
from kernel import response as http_response  # noqa: E402
from kernel import routing as http_routing  # noqa: E402
from kernel.auth import LocalAuth  # noqa: E402
from providers import load_registry, ProviderRegistry  # noqa: E402
from services import chat_persistence  # noqa: E402
from services import chat_service  # noqa: E402
from services import common as service_common  # noqa: E402
from services import conversation_service  # noqa: E402
from services import diagnostics_service  # noqa: E402
from services import docx_service  # noqa: E402
from services import library_service  # noqa: E402
from services import observability_service  # noqa: E402
from services import project_service  # noqa: E402
from services import provider_service  # noqa: E402
from services import settings_service  # noqa: E402


def app_root() -> Path:
    env_root = os.environ.get("STEELG8_APP_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def default_port() -> int:
    raw = os.environ.get("STEELG8_PORT", "8765")
    try:
        port = int(raw)
    except ValueError:
        return 8765
    return port if 1 <= port <= 65535 else 8765


APP_ROOT = app_root()
DEFAULT_CONFIG_DIR = Path.home() / ".steelg8"
SOUL_PATH = Path(os.environ.get("STEELG8_SOUL_PATH", DEFAULT_CONFIG_DIR / "soul.md")).expanduser()
SOUL_TEMPLATE_PATH = APP_ROOT / "prompts" / "soul.md"
EXAMPLE_PROVIDERS_PATH = APP_ROOT / "config" / "providers.example.json"
AUTH = LocalAuth.from_env()


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


ROUTES: tuple[http_routing.Route, ...] = (
    http_routing.Route("GET", "/health", "_get_health", auth_required=False),
    http_routing.Route("GET", "/providers", "_get_providers"),
    http_routing.Route("GET", "/providers/validate", "_get_provider_validation"),
    http_routing.Route("GET", "/usage/summary", "_get_usage_summary"),
    http_routing.Route("GET", "/usage/recent", "_get_usage_recent"),
    http_routing.Route("GET", "/scratch/note", "_get_scratch_note"),
    http_routing.Route("GET", "/templates", "_get_templates"),
    http_routing.Route("GET", "/knowledge", "_get_knowledge"),
    http_routing.Route("GET", "/wallet", "_get_wallet"),
    http_routing.Route("GET", "/preferences", "_get_preferences"),
    http_routing.Route("GET", "/preferences/workspace-allowlist", "_get_workspace_allowlist"),
    http_routing.Route("GET", "/project", "_get_project"),
    http_routing.Route("GET", "/project/conversation", "_get_project_conversation"),
    http_routing.Route("GET", "/project/status", "_get_project_status"),
    http_routing.Route("GET", "/projects", "_get_projects"),
    http_routing.Route("GET", "/conversations", "_get_conversations"),
    http_routing.Route("GET", "/conversations/{conversation_id}/messages", "_get_conversation_messages"),
    http_routing.Route("GET", "/conversations/{conversation_id}", "_get_conversation_detail"),
    http_routing.Route("GET", "/logs", "_get_logs"),
    http_routing.Route("GET", "/diagnostics/doctor", "_get_diagnostics_doctor"),
    http_routing.Route("GET", "/diagnostics/index", "_get_diagnostics_index"),
    http_routing.Route("GET", "/capabilities", "_get_capabilities"),
    http_routing.Route("POST", "/diagnostics/rag-debug", "_post_diagnostics_rag_debug"),
    http_routing.Route("POST", "/providers/reload", "_post_providers_reload"),
    http_routing.Route("POST", "/providers/{provider_name}/sync-models", "_post_provider_sync_models"),
    http_routing.Route("POST", "/providers/{provider_name}/catalog/refresh", "_post_provider_catalog_refresh"),
    http_routing.Route("GET", "/providers/{provider_name}/catalog", "_get_provider_catalog"),
    http_routing.Route("PUT", "/providers/{provider_name}/catalog/selection", "_put_provider_catalog_selection"),
    http_routing.Route("POST", "/chat", "_post_chat"),
    http_routing.Route("POST", "/chat/stream", "_post_chat_stream"),
    http_routing.Route("POST", "/scratch/note", "_post_scratch_note"),
    http_routing.Route("POST", "/project/open", "_post_project_open"),
    http_routing.Route("POST", "/project/close", "_post_project_close"),
    http_routing.Route("POST", "/project/reindex", "_post_project_reindex"),
    http_routing.Route("POST", "/projects/{project_id}/activate", "_post_project_activate"),
    http_routing.Route("POST", "/projects/{project_id}/rename", "_post_project_rename"),
    http_routing.Route("POST", "/preferences", "_post_preferences"),
    http_routing.Route("POST", "/preferences/workspace-allowlist", "_post_workspace_allowlist"),
    http_routing.Route("POST", "/conversations", "_post_conversations"),
    http_routing.Route("POST", "/conversations/{conversation_id}/rename", "_post_conversation_rename"),
    http_routing.Route("POST", "/skills/docx/placeholders", "_post_docx_placeholders"),
    http_routing.Route("POST", "/skills/docx/fill", "_post_docx_fill"),
    http_routing.Route("POST", "/skills/docx/headings", "_post_docx_headings"),
    http_routing.Route("POST", "/skills/docx/insert-section", "_post_docx_insert_section"),
    http_routing.Route("POST", "/skills/docx/append-paragraphs", "_post_docx_append_paragraphs"),
    http_routing.Route("POST", "/skills/docx/append-row", "_post_docx_append_row"),
    http_routing.Route("DELETE", "/templates/{path:path}", "_delete_template"),
    http_routing.Route("DELETE", "/conversations/{conversation_id}", "_delete_conversation"),
    http_routing.Route("DELETE", "/projects/{project_id}", "_delete_project"),
)


class SteelG8Handler(BaseHTTPRequestHandler):
    server_version = "steelg8/0.2"
    registry: ProviderRegistry  # 由 main 注入

    # --- CORS 永久开启：本地内核只绑 127.0.0.1，允许 file:// / localhost
    # 所有源调用是工程必需（WKWebView 加载 file://、浏览器调试）
    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-SteelG8-Token, Accept")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    # ------------------- handlers -------------------

    def _dispatch(self, method: str) -> None:
        match = http_routing.resolve(method, self._path_only(), ROUTES)
        if match is None:
            self.respond(404, {"error": "not found"})
            return
        if match.auth_required and not self._require_auth():
            return
        handler = getattr(self, match.handler_name)
        handler(**match.params)

    def _get_health(self) -> None:
        # /health 是 auth handshake 入口：未鉴权调用只能看到握手字段，
        # 鉴权通过后才暴露 mode / defaultModel / providerSource 这类
        # 同机其他进程不应直接读取的运行细节。
        ensure_soul_file()
        authenticated = AUTH.is_authenticated(self)
        payload: dict[str, Any] = {
            "ok": True,
            "authRequired": AUTH.required,
            "authenticated": authenticated,
        }
        if authenticated:
            payload["mode"] = self._mode_label()
            payload["defaultModel"] = self.registry.default_model
            payload["providerSource"] = self.registry.source
        self.respond(200, payload)

    def _get_providers(self) -> None:
        self.respond(200, provider_service.providers_summary(self.registry))

    def _get_provider_validation(self) -> None:
        self.respond(200, provider_service.validation_summary(self.registry))

    def _get_usage_summary(self) -> None:
        self.respond(200, observability_service.usage_summary())

    def _get_usage_recent(self) -> None:
        query = http_request.query_params(self.path)
        limit = observability_service.bounded_limit(query, default=100, maximum=1000)
        self.respond(200, observability_service.recent_usage(limit=limit))

    def _get_scratch_note(self) -> None:
        self.respond(200, library_service.scratch_note())

    def _get_templates(self) -> None:
        self.respond(200, library_service.templates())

    def _get_knowledge(self) -> None:
        self.respond(200, library_service.knowledge_cards())

    def _get_wallet(self) -> None:
        self.respond(200, provider_service.wallet_summary(self.registry))

    def _get_preferences(self) -> None:
        self.respond(200, settings_service.load_preferences())

    def _get_project(self) -> None:
        self.respond(200, project_service.active_project())

    def _get_project_conversation(self) -> None:
        self.respond(200, project_service.project_conversation())

    def _get_project_status(self) -> None:
        self.respond(200, project_service.index_status())

    def _get_projects(self) -> None:
        self.respond(200, project_service.list_projects())

    def _get_conversations(self) -> None:
        self.respond(200, conversation_service.list_conversations(limit=100))

    def _get_conversation_messages(self, conversation_id: str) -> None:
        cid = self._parse_int(conversation_id, error="bad conversation id")
        if cid is None:
            return
        self._respond_service(conversation_service.conversation_messages, cid)

    def _get_conversation_detail(self, conversation_id: str) -> None:
        cid = self._parse_int(conversation_id, error="bad conversation id")
        if cid is None:
            return
        self._respond_service(conversation_service.conversation_detail, cid)

    def _get_logs(self) -> None:
        self.respond(200, observability_service.logs(http_request.query_params(self.path)))

    def _get_diagnostics_doctor(self) -> None:
        self.respond(
            200,
            diagnostics_service.doctor(
                self.registry,
                diagnostics_service.DiagnosticContext(
                    app_root=APP_ROOT,
                    port=default_port(),
                    auth_required=AUTH.required,
                ),
            ),
        )

    def _get_diagnostics_index(self) -> None:
        self.respond(200, diagnostics_service.index_inspector())

    def _get_capabilities(self) -> None:
        self.respond(200, provider_service.capability_profiles())

    def _post_diagnostics_rag_debug(self) -> None:
        self._respond_service(project_service.rag_debug, self.read_json(), self.registry)

    def _post_providers_reload(self) -> None:
        result = provider_service.reload_registry(
            example_candidates=(EXAMPLE_PROVIDERS_PATH,)
        )
        type(self).registry = result.registry
        self.respond(200, result.payload)

    def _post_provider_sync_models(self, provider_name: str) -> None:
        self._respond_service(provider_service.sync_models, self.registry, provider_name)

    def _post_provider_catalog_refresh(self, provider_name: str) -> None:
        self._respond_service(provider_service.catalog_refresh, self.registry, provider_name)

    def _get_provider_catalog(self, provider_name: str) -> None:
        self._respond_service(provider_service.read_catalog, provider_name)

    def _put_provider_catalog_selection(self, provider_name: str) -> None:
        self._respond_service(
            provider_service.update_catalog_selection,
            provider_name,
            self.read_json(),
        )

    def _post_chat(self) -> None:
        self._handle_chat(stream=False)

    def _post_chat_stream(self) -> None:
        self._handle_chat(stream=True)

    def _post_scratch_note(self) -> None:
        self._respond_service(library_service.save_scratch_note, self.read_json())

    def _post_project_open(self) -> None:
        self._respond_service(project_service.open_project, self.read_json(), self.registry)

    def _post_project_close(self) -> None:
        self._respond_service(project_service.close_project)

    def _post_project_reindex(self) -> None:
        self._respond_service(project_service.reindex_project, self.registry)

    def _post_project_activate(self, project_id: str) -> None:
        pid = self._parse_int(project_id, error="bad project id")
        if pid is None:
            return
        self._respond_service(project_service.activate_project, pid)

    def _post_project_rename(self, project_id: str) -> None:
        pid = self._parse_int(project_id, error="bad project id")
        if pid is None:
            return
        self._respond_service(project_service.rename_project, pid, self.read_json())

    def _post_preferences(self) -> None:
        self._respond_service(settings_service.save_preferences, self.read_json())

    def _get_workspace_allowlist(self) -> None:
        self.respond(200, settings_service.get_workspace_allowlist())

    def _post_workspace_allowlist(self) -> None:
        self._respond_service(settings_service.save_workspace_allowlist, self.read_json())

    def _post_conversations(self) -> None:
        self._respond_service(conversation_service.create_conversation, self.read_json())

    def _post_conversation_rename(self, conversation_id: str) -> None:
        cid = self._parse_int(conversation_id, error="bad conversation id")
        if cid is None:
            return
        self._respond_service(conversation_service.rename_conversation, cid, self.read_json())

    def _post_docx_placeholders(self) -> None:
        self._respond_service(docx_service.placeholders, self.read_json())

    def _post_docx_fill(self) -> None:
        self._respond_service(docx_service.fill, self.read_json())

    def _post_docx_headings(self) -> None:
        self._respond_service(docx_service.headings, self.read_json())

    def _post_docx_insert_section(self) -> None:
        self._respond_service(docx_service.insert_section, self.read_json())

    def _post_docx_append_paragraphs(self) -> None:
        self._respond_service(docx_service.append_paragraphs, self.read_json())

    def _post_docx_append_row(self) -> None:
        self._respond_service(docx_service.append_row, self.read_json())

    def _delete_template(self, path: str) -> None:
        self._respond_service(library_service.delete_template, path)

    def _delete_conversation(self, conversation_id: str) -> None:
        cid = self._parse_int(conversation_id, error="bad conversation id")
        if cid is None:
            return
        self._respond_service(conversation_service.remove_conversation, cid)

    def _delete_project(self, project_id: str) -> None:
        pid = self._parse_int(project_id, error="bad project id")
        if pid is None:
            return
        self._respond_service(project_service.remove_project, pid)

    def _path_only(self) -> str:
        return http_request.path_only(self.path)

    def _require_auth(self) -> bool:
        if AUTH.is_authenticated(self):
            return True
        self.respond(401, AUTH.unauthorized_payload())
        return False

    def _parse_int(self, raw: str, *, error: str) -> int | None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            self.respond(400, {"error": error})
            return None

    def _respond_service(self, fn: Any, *args: Any) -> None:
        try:
            result = fn(*args)
        except service_common.ServiceError as exc:
            self.respond(exc.status, exc.payload)
            return
        if isinstance(result, service_common.ServiceResponse):
            self.respond(result.status, result.payload)
        else:
            self.respond(200, result)

    def _handle_chat(self, *, stream: bool) -> None:
        try:
            prepared = chat_service.prepare_chat(
                self.read_json(),
                self.registry,
                soul_text=ensure_soul_file(),
                stream_endpoint=stream,
            )
        except chat_service.ChatRequestError as exc:
            self.respond(400, {"error": str(exc)})
            return

        if stream:
            self._stream_response(prepared)
        else:
            self.respond(200, chat_service.run_once(prepared))

    def _stream_response(self, prepared: chat_service.PreparedChat) -> None:
        # SSE 头 —— 明确告诉客户端 / 服务器自己：这条响应完了就关 TCP，
        # 否则 http.server 的 keep-alive 会把 socket 挂住，前端 reader
        # 收不到 EOF，一直等不到流结束。
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        # 强制 handler 处理完当前请求就关 socket
        self.close_connection = True

        def write_event(event: dict[str, Any]) -> None:
            try:
                self.wfile.write(http_response.sse_event(event))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                raise

        try:
            write_event(chat_service.conversation_event(prepared))
        except (BrokenPipeError, ConnectionResetError):
            return

        # 先把 rag_hits 作为专门事件发出去（meta 事件之前前端就能挂 UI）
        rag_event = chat_service.rag_event(prepared)
        if rag_event:
            try:
                write_event(rag_event)
            except (BrokenPipeError, ConnectionResetError):
                return

        captured_usage: dict[str, int] | None = None
        captured_model: str | None = None
        captured_full: str = ""
        captured_transcript: list[dict[str, Any]] = []
        captured_parts: list[str] = []
        try:
            for event in chat_service.stream_events(prepared):
                etype = event.get("type")
                if etype == "_transcript":
                    msg = event.get("message")
                    if isinstance(msg, dict):
                        captured_transcript.append(msg)
                    continue
                if etype == "usage":
                    captured_usage = event.get("usage")
                    captured_model = event.get("model")
                elif etype == "done":
                    captured_full = event.get("full") or ""
                elif etype == "delta":
                    captured_parts.append(str(event.get("content") or ""))
                write_event(event)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端断开：仍然持久化已生成的内容
            partial_full = captured_full or "".join(captured_parts)
            if partial_full or captured_transcript:
                try:
                    chat_persistence.persist_stream_partial(
                        prepared,
                        transcript=captured_transcript,
                        content=partial_full,
                        usage_payload=captured_usage,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "chat.stream.persist_partial_failed",
                        exc=exc,
                        conversation_id=prepared.conversation_id,
                        content_len=len(partial_full),
                        transcript_count=len(captured_transcript),
                    )
            return

        # 持久化 tool transcript + assistant 文本
        final_full = captured_full or "".join(captured_parts)
        if captured_transcript or final_full.strip():
            try:
                chat_persistence.persist_stream_final(
                    prepared,
                    transcript=captured_transcript,
                    content=final_full,
                    usage_payload=captured_usage,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "chat.stream.persist_final_failed",
                    exc=exc,
                    conversation_id=prepared.conversation_id,
                    content_len=len(final_full),
                    transcript_count=len(captured_transcript),
                )

        # 流结束后写 usage log（mock 走 layer==mock，不记账）
        chat_persistence.record_stream_usage(
            prepared,
            usage_payload=captured_usage,
            model=captured_model,
        )

    # ------------------- io utilities -------------------

    def read_json(self) -> Any:
        return http_request.read_json(self.headers, self.rfile)

    def respond(self, status: int, payload: dict[str, Any]) -> None:
        body = http_response.json_body(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # 关键：不能阻塞写 stderr —— Swift 侧若长时间不读 pipe，
        # 64KB buffer 满了就会把所有 handler 线程卡住。
        # 默认静默；设 STEELG8_HTTP_LOG=1 才写到独立日志文件。
        if not os.environ.get("STEELG8_HTTP_LOG"):
            return
        try:
            log_path = Path.home() / ".steelg8" / "access.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{format % args}\n")
        except OSError:
            pass

    def _mode_label(self) -> str:
        if self.registry.first_ready() is not None:
            return f"provider-registry ({self.registry.source})"
        return "mock-fallback"


def main() -> None:
    parser = argparse.ArgumentParser(description="steelg8 Phase 0.5 local server")
    parser.add_argument("--port", type=int, default=default_port())
    args = parser.parse_args()

    ensure_soul_file()

    # 启动早期：检查 ~/.steelg8 配置文件是否需要从旧单文件迁移到三份。
    # 失败 fail-closed —— 不进入 serve_forever，让用户看到原因（损坏的 providers.json
    # 强行启动只会以一个空 registry 假装正常）。
    try:
        migration_result = config_migrate.run_if_needed()
    except config_migrate.ConfigMigrationError as exc:
        print(
            json.dumps(
                {"event": "config_migrate_failed", "error": str(exc)},
                ensure_ascii=False,
            ),
            flush=True,
        )
        sys.exit(2)
    if migration_result.get("action") not in (None, "noop"):
        print(
            json.dumps(
                {"event": "config_migrated", **migration_result},
                ensure_ascii=False,
            ),
            flush=True,
        )

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
