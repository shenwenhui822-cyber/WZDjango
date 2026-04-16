"""
产品净值 vs rq_bench 基准：计算结果落库（basic_rq 库内 calc_* 集合）。

约定：
- 原始行情：`rq_bench`
- 计算结果：集合名必须以 settings.MONGODB_RQ_BENCH_CALC_PREFIX（默认 calc_）开头
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pymongo
from django.conf import settings
from django.utils import timezone

from portal.db.mongo import get_rq_bench_calc_collection


def ensure_nav_bench_daily_indexes() -> None:
    """日频对比表：按 (report_date, product_name, bench_code) 唯一。"""
    coll = get_rq_bench_calc_collection(settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_DAILY)
    try:
        coll.create_index(
            [("report_date", pymongo.ASCENDING), ("product_name", pymongo.ASCENDING), ("bench_code", pymongo.ASCENDING)],
            unique=True,
            name="uniq_nav_bench_daily",
            background=True,
        )
    except Exception:
        pass


def ensure_nav_bench_summary_indexes() -> None:
    """区间汇总表：按 (product_name, bench_code, date_from, date_to) 唯一。"""
    coll = get_rq_bench_calc_collection(settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_SUMMARY)
    try:
        coll.create_index(
            [
                ("product_name", pymongo.ASCENDING),
                ("bench_code", pymongo.ASCENDING),
                ("date_from", pymongo.ASCENDING),
                ("date_to", pymongo.ASCENDING),
            ],
            unique=True,
            name="uniq_nav_bench_summary",
            background=True,
        )
    except Exception:
        pass


def _now_aware() -> datetime:
    now = timezone.now()
    if timezone.is_naive(now):
        return timezone.make_aware(now, timezone.get_current_timezone())
    return now


def upsert_nav_bench_daily_row(doc: dict[str, Any]) -> None:
    """
    写入/更新一条日频对比行。建议字段见 rq_bench_nav_compare.md。
    过滤键：report_date + product_name + bench_code
    """
    coll = get_rq_bench_calc_collection(settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_DAILY)
    flt = {
        "report_date": doc.get("report_date"),
        "product_name": doc.get("product_name"),
        "bench_code": doc.get("bench_code"),
    }
    payload = {**doc, "updated_at": _now_aware(), "_schema": "calc_nav_bench_daily"}
    coll.update_one(flt, {"$set": payload}, upsert=True)


def upsert_nav_bench_summary_row(doc: dict[str, Any]) -> None:
    """写入/更新一条区间汇总行。"""
    coll = get_rq_bench_calc_collection(settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_SUMMARY)
    flt = {
        "product_name": doc.get("product_name"),
        "bench_code": doc.get("bench_code"),
        "date_from": doc.get("date_from"),
        "date_to": doc.get("date_to"),
    }
    payload = {**doc, "updated_at": _now_aware(), "_schema": "calc_nav_bench_summary"}
    coll.update_one(flt, {"$set": payload}, upsert=True)
