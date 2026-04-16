"""产品净值 vs rq_bench 基准：计算、落库与页面展示数据组装。"""
from __future__ import annotations

from typing import Any

from django.conf import settings

from portal.db.mongo import get_rq_bench_calc_collection, get_rq_bench_collection
from portal.services.rq_bench_calc_store import (
    ensure_nav_bench_daily_indexes,
    ensure_nav_bench_summary_indexes,
    upsert_nav_bench_daily_row,
    upsert_nav_bench_summary_row,
)
from portal.services.trade_calendar_service import fetch_nav_curve_series

# 产品前缀 -> 基准 code（按业务映射）
_BENCH_RULES: list[tuple[str, str]] = [
    ("中证1000指增", "000852.SZ"),
    ("中证500指增", "000905.SZ"),
    ("双创选股", "931643"),
    ("尊选", "000852.SZ"),
    ("沪深300指增", "000300.SH"),
    ("红利", "000015.SH"),
    ("量化对冲", "000852.SZ"),
    ("量化精选", "000852.SZ"),
]


_BENCH_CODE_TO_NAME: dict[str, str] = {
    "000001.SH": "上证综指（上海证券综合指数）",
    "399001.SZ": "深证成指（深证成份指数）",
    "881001.WI": "国证A指（替代万得全A指数）",
    "000300.SH": "沪深300指数",
    "000905.SZ": "中证500指数",
    "000852.SZ": "中证1000指数",
    "931643": "科创创业50指数",
    "000015.SH": "上证红利指数",
}


def resolve_bench_code(product_name: str) -> str:
    name = (product_name or "").strip()
    for prefix, code in _BENCH_RULES:
        if name.startswith(prefix):
            return code
    return "000300.SH"


def resolve_bench_name(bench_code: str) -> str:
    code = (bench_code or "").strip()
    return _BENCH_CODE_TO_NAME.get(code, code or "-")


def _build_bench_ret_map(bench_code: str, days: list[str]) -> dict[str, float]:
    coll = get_rq_bench_collection()
    ret_map: dict[str, float] = {}
    for d in coll.find(
        {"code": bench_code, "date": {"$in": days}},
        {"date": 1, "pct_chg": 1, "_id": 0},
    ):
        day = str(d.get("date") or "")[:10]
        if not day:
            continue
        try:
            ret_map[day] = float(d.get("pct_chg"))
        except (TypeError, ValueError):
            continue
    return ret_map


def _summary_from_rows(
    *,
    product_name: str,
    bench_code: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if not rows:
        return {
            "product_name": product_name,
            "bench_code": bench_code,
            "date_from": "",
            "date_to": "",
            "days": 0,
            "product_ret_cum": None,
            "bench_ret_cum": None,
            "excess_ret_cum": None,
        }

    first = rows[0]
    last = rows[-1]
    p0 = first.get("product_nav_norm")
    p1 = last.get("product_nav_norm")
    b0 = first.get("bench_nav_norm")
    b1 = last.get("bench_nav_norm")
    p_cum = (p1 / p0 - 1) if p0 not in (None, 0) and p1 is not None else None
    b_cum = (b1 / b0 - 1) if b0 not in (None, 0) and b1 is not None else None
    ex_cum = (p1 / b1 - 1) if b1 not in (None, 0) and p1 is not None else None
    return {
        "product_name": product_name,
        "bench_code": bench_code,
        "date_from": first.get("report_date") or "",
        "date_to": last.get("report_date") or "",
        "days": len(rows),
        "product_ret_cum": p_cum,
        "bench_ret_cum": b_cum,
        "excess_ret_cum": ex_cum,
    }


def _load_cached_compare(
    *,
    product_name: str,
    bench_code: str,
) -> dict[str, Any] | None:
    daily_coll = get_rq_bench_calc_collection(settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_DAILY)
    rows = list(
        daily_coll.find(
            {"product_name": product_name, "bench_code": bench_code},
            {"_id": 0, "updated_at": 0, "_schema": 0},
        ).sort("report_date", 1)
    )
    if not rows:
        return None

    summary_coll = get_rq_bench_calc_collection(settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_SUMMARY)
    summary = summary_coll.find_one(
        {"product_name": product_name, "bench_code": bench_code},
        {"_id": 0, "updated_at": 0, "_schema": 0},
        sort=[("date_to", -1)],
    )
    if not summary:
        summary = _summary_from_rows(product_name=product_name, bench_code=bench_code, rows=rows)

    return {
        "product_name": product_name,
        "bench_code": bench_code,
        "bench_name": resolve_bench_name(bench_code),
        "rows": rows,
        "summary": summary,
        "calc_daily_collection": settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_DAILY,
        "calc_summary_collection": settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_SUMMARY,
        "from_cache": True,
    }


def build_and_store_nav_bench_compare(
    *,
    product_name: str,
) -> dict[str, Any]:
    """
    计算产品 vs 基准对比并落库（basic_rq.calc_*）。
    若集合不存在，会在 upsert/建索引时自动创建。
    固定口径：从该产品最早净值日开始，计算至最新净值日（仅交易日）。
    """
    pn = (product_name or "").strip()
    if not pn:
        raise ValueError("请选择产品名称")
    bench_code = resolve_bench_code(pn)

    cached = _load_cached_compare(product_name=pn, bench_code=bench_code)
    if cached is not None:
        return cached

    nav_points = fetch_nav_curve_series(
        product_name=pn,
        date_from=None,
        date_to=None,
        only_trading_days=True,
        recent_trading_days=None,
    )
    if not nav_points:
        return {
            "product_name": pn,
            "bench_code": bench_code,
            "bench_name": resolve_bench_name(bench_code),
            "rows": [],
            "summary": _summary_from_rows(product_name=pn, bench_code=bench_code, rows=[]),
            "calc_daily_collection": settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_DAILY,
            "calc_summary_collection": settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_SUMMARY,
            "from_cache": False,
        }

    days = [str(x.get("report_date") or "")[:10] for x in nav_points if x.get("report_date")]
    bench_ret_map = _build_bench_ret_map(bench_code, days)

    first_nav = float(nav_points[0]["current_nav"])
    prev_nav: float | None = None
    bench_norm = 1.0
    rows: list[dict[str, Any]] = []
    for p in nav_points:
        day = str(p.get("report_date") or "")[:10]
        nav = float(p["current_nav"])
        p_ret = (nav / prev_nav - 1.0) if prev_nav not in (None, 0) else None
        b_ret = bench_ret_map.get(day)
        if b_ret is not None:
            bench_norm = bench_norm * (1.0 + float(b_ret))
        p_norm = nav / first_nav if first_nav else None
        ex_ret = (p_ret - b_ret) if (p_ret is not None and b_ret is not None) else None
        ex_cum = (p_norm / bench_norm - 1.0) if (p_norm is not None and bench_norm) else None
        row = {
            "report_date": day,
            "product_name": pn,
            "bench_code": bench_code,
            "product_nav": nav,
            "product_ret": p_ret,
            "bench_ret": b_ret,
            "excess_ret": ex_ret,
            "product_nav_norm": p_norm,
            "bench_nav_norm": bench_norm,
            "excess_cum": ex_cum,
        }
        rows.append(row)
        prev_nav = nav

    ensure_nav_bench_daily_indexes()
    ensure_nav_bench_summary_indexes()
    for row in rows:
        upsert_nav_bench_daily_row(row)
    summary = _summary_from_rows(product_name=pn, bench_code=bench_code, rows=rows)
    upsert_nav_bench_summary_row(summary)

    return {
        "product_name": pn,
        "bench_code": bench_code,
        "bench_name": resolve_bench_name(bench_code),
        "rows": rows,
        "summary": summary,
        "calc_daily_collection": settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_DAILY,
        "calc_summary_collection": settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_SUMMARY,
        "from_cache": False,
    }

