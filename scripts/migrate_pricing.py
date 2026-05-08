#!/usr/bin/env python3
"""
一次性 catalog pricing 清洗 + scrape 触发。

做两件事：
1. 对所有 pricing_source=fallback 的条目，按新版 pricing.lookup 重算；
   找不到精确价 → 写 null（替换掉老 PROVIDER_DEFAULT 撒谎值）。
2. 对 kimi / deepseek 触发 pricing_scraper.scrape_pricing；
   返回非空就升级为 verified。

跑完后建议 ⌘, 进设置 → 供应商管理 → 刷新一下，让 kernel 重读 registry。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 让脚本能 import Python/ 下的 stdlib 模块
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Python"))

import model_catalog  # noqa: E402
import pricing  # noqa: E402
from services import pricing_scraper  # noqa: E402


def _normalize_pair(p: pricing.Price | None) -> dict[str, float | None]:
    if p is None:
        return {"input": None, "output": None}
    return {
        "input": p.input_per_1m if p.input_per_1m > 0 else None,
        "output": p.output_per_1m if p.output_per_1m > 0 else None,
    }


def step1_null_all_non_verified() -> tuple[int, int]:
    """按用户偏好：所有非 verified 条目的价格清成 null + pricing_source=fallback。
    不再用静态表撒谎兜底。"""
    doc = model_catalog.load()
    providers = doc.get("providers") or {}
    touched = 0
    nulled = 0
    for pid, prov in providers.items():
        if not isinstance(prov, dict):
            continue
        for m in prov.get("models") or []:
            if not isinstance(m, dict):
                continue
            if m.get("pricing_source") == "verified":
                continue
            old = m.get("pricing_per_mtoken") or {}
            had_value = isinstance(old.get("input"), (int, float)) or isinstance(
                old.get("output"), (int, float)
            )
            if had_value or m.get("pricing_source") != "fallback":
                m["pricing_per_mtoken"] = {"input": None, "output": None}
                m["pricing_source"] = "fallback"
                touched += 1
                if had_value:
                    nulled += 1
    model_catalog.save(doc)
    return touched, nulled


def step2_run_scrapers() -> dict[str, int]:
    """kimi / deepseek / openrouter（暂占位）跑一次爬虫，写 verified。"""
    counts: dict[str, int] = {}
    for pid in ("kimi", "deepseek", "bailian"):
        try:
            scraped = pricing_scraper.scrape_pricing(pid) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"  [{pid}] scrape failed: {exc}")
            scraped = {}
        if not scraped:
            counts[pid] = 0
            continue

        # 只把 catalog 里已经存在的 model id 升 verified（避免引入幽灵条目）
        existing_ids = {m["id"] for m in model_catalog.all_models(pid)}
        applied = 0
        for mid, price in scraped.items():
            if mid not in existing_ids:
                continue
            ok = model_catalog.record_pricing(
                pid, mid, price, pricing_source="verified"
            )
            if ok:
                applied += 1
        counts[pid] = applied
    return counts


def main() -> None:
    catalog_path = Path(
        os.environ.get(
            "STEELG8_CATALOG_PATH",
            Path.home() / ".steelg8" / "model_catalog.json",
        )
    ).expanduser()
    if not catalog_path.exists():
        print(f"catalog 文件不存在：{catalog_path}")
        return

    print(f"==> catalog: {catalog_path}")

    print("\n[1/2] 清掉所有非 verified 条目的旧价（撒谎的 fallback）")
    touched, nulled = step1_null_all_non_verified()
    print(f"    touched={touched}, nulled={nulled}")

    print("\n[2/2] 跑 pricing scraper（LiteLLM JSON）写 verified")
    counts = step2_run_scrapers()
    for pid, n in counts.items():
        print(f"    [{pid}] applied verified count = {n}")

    print("\n完成。建议进设置 → 供应商管理 → 刷新一下让 kernel 重读 registry。")


if __name__ == "__main__":
    main()
