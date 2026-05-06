"""
钱包：查各家 provider 的账户余额 / 额度
-----------------------------------------

并不是每家都有公开余额 API：

- Kimi / Moonshot：有 ✅
    GET https://api.moonshot.cn/v1/users/me/balance
    → {"code":0, "data":{"available_balance":..., "voucher_balance":..., "cash_balance":...}}
- 阿里云百炼：❌ 没有公开"账户余额 API"（只能跳控制台看）
- DeepSeek：也有一个 /user/balance 端点 ✅
    GET https://api.deepseek.com/user/balance
- OpenRouter：有 key 详情 ✅
    GET https://openrouter.ai/api/v1/key
- 其它（智谱 / 豆包 / 硅基流动等）：暂时留空

统一返回：
  {
    "items": [
      {
        "provider": "kimi",
        "name": "Kimi / Moonshot",
        "available_usd": 1.23,     # 可用余额（统一换算美元，没 key / 查不到时为 null）
        "raw": {...},               # 原始返回
        "console_url": "...",       # 去官网查看的链接
        "status": "ok" | "missing_key" | "no_api" | "error",
        "error": str | null,
      },
      ...
    ]
  }
"""

from __future__ import annotations

from typing import Any

from providers import ProviderRegistry
import network


# 粗略的 CNY → USD 汇率，用于 Kimi / DeepSeek 的 CNY 余额换算
CNY_TO_USD = 1 / 7.2


def _get_json(url: str, *, api_key: str = "", timeout: int = 8) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = network.request_json(
        url,
        method="GET",
        headers=headers,
        timeout=timeout,
        retries=1,
    )
    return body if isinstance(body, dict) else {}


def _check_kimi(prov) -> dict[str, Any]:
    out = {
        "provider": "kimi",
        "name": "Kimi / Moonshot",
        "currency": "CNY",
        "console_url": "https://platform.moonshot.cn/console/account/usage",
        "status": "missing_key",
        "available": None,
        "available_usd": None,
        "raw": None,
        "error": None,
    }
    if not prov or not prov.is_ready():
        return out
    try:
        body = _get_json(
            f"{prov.base_url}/users/me/balance",
            api_key=prov.api_key(),
        )
    except network.NetworkError as exc:
        out["status"] = "error"
        out["error"] = str(exc)
        return out
    except Exception as exc:  # noqa: BLE001
        out["status"] = "error"
        out["error"] = str(exc)
        return out

    out["raw"] = body
    # Moonshot 返回 {code:0, data:{available_balance, voucher_balance, cash_balance}}
    data = (body or {}).get("data") or {}
    available = data.get("available_balance")
    if available is not None:
        out["available"] = float(available)
        out["available_usd"] = round(float(available) * CNY_TO_USD, 4)
        out["status"] = "ok"
    return out


def _check_deepseek(prov) -> dict[str, Any]:
    out = {
        "provider": "deepseek",
        "name": "DeepSeek",
        "currency": "USD",
        "console_url": "https://platform.deepseek.com/usage",
        "status": "missing_key",
        "available": None,
        "available_usd": None,
        "raw": None,
        "error": None,
    }
    if not prov or not prov.is_ready():
        return out
    try:
        body = _get_json(
            f"{prov.base_url}/user/balance",
            api_key=prov.api_key(),
        )
    except network.NetworkError as exc:
        out["status"] = "error"
        out["error"] = str(exc)
        return out
    except Exception as exc:  # noqa: BLE001
        out["status"] = "error"
        out["error"] = str(exc)
        return out
    out["raw"] = body
    # DeepSeek 返回 {is_available, balance_infos:[{currency:"CNY", total_balance:"10.0"}]}
    infos = (body or {}).get("balance_infos") or []
    for info in infos:
        bal = float(info.get("total_balance", 0) or 0)
        currency = (info.get("currency") or "").upper()
        out["currency"] = currency
        out["available"] = bal
        out["available_usd"] = round(bal * (CNY_TO_USD if currency == "CNY" else 1.0), 4)
        out["status"] = "ok"
        break
    return out


def _check_openrouter(prov) -> dict[str, Any]:
    out = {
        "provider": "openrouter",
        "name": "OpenRouter",
        "currency": "USD",
        "console_url": "https://openrouter.ai/credits",
        "status": "missing_key",
        "available": None,
        "available_usd": None,
        "raw": None,
        "error": None,
    }
    if not prov or not prov.is_ready():
        return out
    try:
        body = _get_json(
            f"{prov.base_url}/key",
            api_key=prov.api_key(),
        )
    except Exception as exc:  # noqa: BLE001
        out["status"] = "error"
        out["error"] = str(exc)
        return out
    out["raw"] = body
    data = (body or {}).get("data") or {}
    # OpenRouter 返回 {data:{limit: 10, usage: 2.5, ...}}
    limit = data.get("limit")
    usage = data.get("usage")
    if limit is not None and usage is not None:
        avail = float(limit) - float(usage)
        out["available"] = round(avail, 4)
        out["available_usd"] = round(avail, 4)
        out["status"] = "ok"
    elif usage is not None:
        # 无 limit 表示按量付费，展示已用量
        out["available"] = -float(usage)
        out["available_usd"] = -round(float(usage), 4)
        out["status"] = "ok"
    return out


def _check_bailian(prov) -> dict[str, Any]:
    # 百炼没有公开余额 API；只标状态 + 链接
    return {
        "provider": "bailian",
        "name": "阿里百炼",
        "currency": "CNY",
        "console_url": "https://bailian.console.aliyun.com/?tab=model#/api-key",
        "status": "no_api" if (prov and prov.is_ready()) else "missing_key",
        "available": None,
        "available_usd": None,
        "raw": None,
        "error": "百炼暂无公开余额查询 API，请去控制台查看。",
    }


def _check_generic(prov_name: str, prov) -> dict[str, Any]:
    return {
        "provider": prov_name,
        "name": prov_name,
        "currency": "",
        "console_url": "",
        "status": "no_api" if (prov and prov.is_ready()) else "missing_key",
        "available": None,
        "available_usd": None,
        "raw": None,
        "error": None,
    }


def summary(registry: ProviderRegistry) -> dict[str, Any]:
    """一次查所有家，并行慢也没事，总共 4-5 家，最多 ~30 秒。"""
    import concurrent.futures as cf
    checks: dict[str, Any] = {
        "kimi":       _check_kimi,
        "deepseek":   _check_deepseek,
        "openrouter": _check_openrouter,
        "bailian":    _check_bailian,
    }
    items: list[dict[str, Any]] = []
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(fn, registry.providers.get(name)): name
            for name, fn in checks.items()
        }
        for fut in cf.as_completed(futures, timeout=30):
            try:
                items.append(fut.result(timeout=10))
            except Exception as exc:  # noqa: BLE001
                items.append({
                    "provider": futures[fut],
                    "status": "error",
                    "error": str(exc),
                    "available": None,
                    "available_usd": None,
                })

    # 其它就绪的 provider（比如 zhipu / doubao）也列出来，让用户知道不支持
    for pname, prov in (registry.providers or {}).items():
        if pname in checks:
            continue
        items.append(_check_generic(pname, prov))

    # 按 status 排序：ok > no_api > missing_key > error
    order = {"ok": 0, "no_api": 1, "missing_key": 2, "error": 3}
    items.sort(key=lambda x: order.get(x.get("status", "error"), 9))

    total_usd = sum(
        i.get("available_usd") or 0
        for i in items
        if i.get("status") == "ok" and (i.get("available_usd") or 0) > 0
    )

    return {
        "items": items,
        "totalAvailableUsd": round(total_usd, 4),
        "cnyRate": 1 / CNY_TO_USD,
    }
