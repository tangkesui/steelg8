"""
Small HTTP helpers shared by provider-facing modules.

The goal is not to hide urllib; it is to make timeouts, HTTP bodies, transient
errors, and retry policy look the same across chat, embedding, rerank, wallet,
and web tools.
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any
from urllib import error, request


class NetworkError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str = "",
        url: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.url = url
        self.retryable = retryable


_STATUS_HINTS: dict[int, str] = {
    400: "请求参数可能不被该 provider 接受",
    401: "认证失败，请检查 API Key",
    403: "无权限或额度/模型权限未开通",
    404: "接口或模型不存在，请检查 base_url / model",
    408: "上游请求超时",
    409: "上游正在处理冲突请求，可稍后重试",
    425: "上游要求稍后重试",
    429: "触发限流或余额不足",
    500: "上游服务内部错误",
    502: "上游网关错误",
    503: "上游服务暂不可用",
    504: "上游网关超时",
}

_RETRYABLE_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


def _request_url(req: request.Request | str) -> str:
    return req.full_url if isinstance(req, request.Request) else req


def _read_error_body(exc: error.HTTPError, *, limit: int = 800) -> str:
    try:
        raw = exc.read()
    except Exception:  # noqa: BLE001
        return ""
    return raw.decode("utf-8", errors="replace")[:limit] if raw else ""


def _format_http_error(status: int, body: str) -> str:
    hint = _STATUS_HINTS.get(status, "HTTP 请求失败")
    return f"HTTP {status}：{hint}" + (f"；{body}" if body else "")


def open_request(
    req: request.Request | str,
    *,
    timeout: int | float,
    retries: int = 0,
    retry_delay: float = 0.4,
) -> Any:
    """Open a urllib request with consistent error messages.

    Retries are opt-in. Use them only for idempotent calls such as embedding,
    rerank, web search/fetch, and wallet checks; chat completions should keep
    retries at zero to avoid duplicate model/tool side effects.
    """
    attempt = 0
    started = time.time()
    while True:
        try:
            resp = request.urlopen(req, timeout=timeout)
            if attempt > 0:
                try:
                    import logger
                    logger.info("http.retry_succeeded",
                                url=_request_url(req),
                                attempts=attempt + 1,
                                duration_ms=int((time.time() - started) * 1000))
                except ImportError:
                    pass
            return resp
        except error.HTTPError as exc:
            body = _read_error_body(exc)
            retryable = exc.code in _RETRYABLE_STATUSES
            if retryable and attempt < retries:
                try:
                    import logger
                    logger.warn("http.retry",
                                url=_request_url(req),
                                status=exc.code,
                                attempt=attempt + 1,
                                of=retries + 1,
                                body=body[:200])
                except ImportError:
                    pass
                time.sleep(retry_delay * (attempt + 1))
                attempt += 1
                continue
            try:
                import logger
                logger.error("http.failed",
                             url=_request_url(req),
                             status=exc.code,
                             body=body[:500],
                             duration_ms=int((time.time() - started) * 1000),
                             retryable=retryable,
                             attempts=attempt + 1)
            except ImportError:
                pass
            raise NetworkError(
                _format_http_error(exc.code, body),
                status=exc.code,
                body=body,
                url=_request_url(req),
                retryable=retryable,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            if attempt < retries:
                time.sleep(retry_delay * (attempt + 1))
                attempt += 1
                continue
            raise NetworkError(
                f"网络超时（{timeout}s）",
                url=_request_url(req),
                retryable=True,
            ) from exc
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            retryable = isinstance(reason, (TimeoutError, socket.timeout, ConnectionError, OSError))
            if retryable and attempt < retries:
                time.sleep(retry_delay * (attempt + 1))
                attempt += 1
                continue
            raise NetworkError(
                f"网络错误：{reason}",
                url=_request_url(req),
                retryable=retryable,
            ) from exc
        except (ConnectionError, OSError) as exc:
            if attempt < retries:
                time.sleep(retry_delay * (attempt + 1))
                attempt += 1
                continue
            raise NetworkError(
                f"网络错误：{exc}",
                url=_request_url(req),
                retryable=True,
            ) from exc


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int | float,
    retries: int = 0,
) -> Any:
    data = None
    final_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/json")
    req = request.Request(url, data=data, headers=final_headers, method=method)
    with open_request(req, timeout=timeout, retries=retries) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise NetworkError(f"响应不是合法 JSON：{raw[:240]}") from exc


def request_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int | float,
    retries: int = 0,
) -> tuple[str, dict[str, str]]:
    req = request.Request(url, headers=headers or {}, method="GET")
    with open_request(req, timeout=timeout, retries=retries) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        resp_headers = dict(resp.headers.items())
    return body, resp_headers
