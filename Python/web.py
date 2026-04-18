"""
Web 搜索 + 网页内容抓取
------------------------

两个工具：
- search(query) → Tavily（需要 API key；tavily 免费档 1000 次/月）
- fetch(url)    → Jina Reader（免费，直接 prefix https://r.jina.ai/）

两者都是给 LLM tool calling 用的，返回结构化 JSON。
"""

from __future__ import annotations

import json
from typing import Any
from urllib import request, error
from urllib.parse import quote

from providers import ProviderRegistry


# ---- Tavily Search ----


class WebError(RuntimeError):
    pass


def _pick_tavily(registry: ProviderRegistry):
    prov = registry.providers.get("tavily")
    if prov and prov.is_ready():
        return prov
    return None


def search(
    query: str,
    registry: ProviderRegistry,
    *,
    max_results: int = 5,
    search_depth: str = "basic",
    timeout: int = 20,
) -> list[dict[str, Any]]:
    """Tavily 搜索。没配 tavily provider 就抛 WebError。"""
    prov = _pick_tavily(registry)
    if prov is None:
        raise WebError(
            "没配 tavily provider。去 https://tavily.com 注册免费额度，"
            "然后在 Settings 里加一家名叫 tavily 的 provider（base_url 随便填，用 API Key 就行）"
        )

    payload = {
        "api_key": prov.api_key(),
        "query": query,
        "max_results": max(1, min(int(max_results), 10)),
        "search_depth": search_depth if search_depth in {"basic", "advanced"} else "basic",
        "include_answer": False,
        "include_raw_content": False,
    }

    req = request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400] if exc.fp else ""
        raise WebError(f"HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise WebError(f"网络错误：{exc}") from exc

    results = body.get("results") or []
    out = []
    for r in results[:max_results]:
        out.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": (r.get("content", "") or "")[:500],
            "score": r.get("score"),
        })
    return out


# ---- Jina Reader fetch ----


def fetch(url: str, *, timeout: int = 25) -> dict[str, Any]:
    """Jina Reader：https://r.jina.ai/<URL> 直接返回 markdown。
    无需 API Key。"""
    if not (url.startswith("http://") or url.startswith("https://")):
        raise WebError("url 必须 http(s):// 开头")

    jina_url = "https://r.jina.ai/" + url  # Jina 自己做 URL encoding
    req = request.Request(
        jina_url,
        headers={
            "User-Agent": "steelg8/0.2 (+file://local)",
            "Accept": "text/markdown, text/plain",
            # Jina Reader 在响应里塞结构化信号
            "X-Return-Format": "markdown",
            "X-With-Links-Summary": "true",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            ctype = resp.headers.get("Content-Type", "")
    except error.HTTPError as exc:
        raise WebError(f"Jina HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise WebError(f"网络错误：{exc}") from exc

    # Jina 返回的 markdown 可能有 "Title: ..." 开头元信息
    title = ""
    lines = body.splitlines()
    for line in lines[:5]:
        if line.startswith("Title:"):
            title = line[len("Title:"):].strip()
            break

    # 内容太长截一下（~20k 字符，约 10k token）
    limit = 20_000
    truncated = False
    if len(body) > limit:
        body = body[:limit]
        truncated = True

    return {
        "url": url,
        "title": title,
        "markdown": body,
        "truncated": truncated,
        "contentType": ctype,
    }
