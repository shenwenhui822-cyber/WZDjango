"""日度持仓分析页：聚合 position_daily 后端数据供 portal 模板渲染。"""
from __future__ import annotations

import logging
import time
from typing import Any

from django.conf import settings

from portal.data.tradelog_account_config import (
    account_meta_for_strategy_tag,
    is_rt_future_account,
    stock_strategy_tags,
)
from position_daily.services.cache import get_daily_report
from position_daily.services.config import STRATEGY_TAG
from position_daily.services.position import (
    get_available_dates,
    get_latest_snapshot_date,
    snapshot_exists,
)

logger = logging.getLogger("position_daily.views")


def _allowed_strategy_tags() -> list[str]:
    """日度持仓仅展示证券账户，不含 rt_future 期货账户。"""
    return stock_strategy_tags()


def resolve_strategy_tag(selected: str | None) -> str:
    allowed = _allowed_strategy_tags()
    sel = (selected or "").strip()
    if sel in allowed:
        return sel
    if allowed:
        return allowed[0]
    return STRATEGY_TAG


def _build_sidebar_items(trade_date: str) -> list[dict[str, Any]]:
    """左侧导航：产品 - 经纪商（仅证券账户）。"""
    from portal.data.tradelog_account_config import ACCOUNT_BRIEF_DISPLAY_ORDER

    items: list[dict[str, Any]] = []
    for entry in ACCOUNT_BRIEF_DISPLAY_ORDER:
        tag = entry["strategy_tag"]
        if is_rt_future_account(tag):
            continue
        product = entry["product"]
        broker = entry.get("broker") or account_meta_for_strategy_tag(tag)["broker"]
        has_data = bool(trade_date) and snapshot_exists(trade_date, strategy_tag=tag)
        items.append(
            {
                "strategy_tag": tag,
                "product": product,
                "broker": broker,
                "label": f"{product} - {broker}",
                "has_data": has_data,
            }
        )
    return items


def build_position_daily_page_context(
    *,
    strategy_tag: str | None = None,
    trade_date: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """返回 portal 模板所需的报告上下文。"""
    tag = resolve_strategy_tag(strategy_tag)
    meta = account_meta_for_strategy_tag(tag)

    day = (trade_date or "").strip()
    if not day or day == "latest":
        day = get_latest_snapshot_date(strategy_tag=tag) or ""

    available_dates = get_available_dates(limit=60, strategy_tag=tag)
    sidebar_items = _build_sidebar_items(day)

    if not day:
        return {
            "trade_date": "",
            "available_dates": available_dates,
            "brief_sidebar_items": sidebar_items,
            "selected_strategy_tag": tag,
            "selected_product": meta.get("product", ""),
            "selected_broker": meta.get("broker", ""),
            "error": "无可用持仓快照",
            "ctx": None,
            "from_cache": False,
            "request_elapsed_ms": 0,
        }

    t0 = time.perf_counter()
    ctx, from_cache = get_daily_report(day, strategy_tag=tag, force_refresh=force_refresh)
    elapsed = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "页面数据 tag=%s date=%s 耗时=%dms cache=%s error=%s",
        tag,
        day,
        elapsed,
        from_cache,
        ctx.error,
    )
    return {
        "trade_date": day,
        "available_dates": available_dates,
        "brief_sidebar_items": sidebar_items,
        "selected_strategy_tag": tag,
        "selected_product": meta.get("product", ""),
        "selected_broker": meta.get("broker", ""),
        "error": ctx.error,
        "ctx": ctx,
        "from_cache": from_cache,
        "debug": settings.DEBUG,
        "request_elapsed_ms": elapsed,
    }
