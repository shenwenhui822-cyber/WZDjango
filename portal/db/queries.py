"""从 MongoDB 读取业务数据。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from portal.data.alpha_daily_schema import ALPHA_DAILY_SCHEMA
from portal.db.mongo import get_app_collection


def _parse_iso_day(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y-%m-%d")


def _report_date_range_clause(
    date_from: str | None, date_to: str | None
) -> dict[str, Any] | None:
    """
    报表日期范围（含端点）。
    兼容 report_date 存 YYYY-MM-DD 字符串或 BSON datetime。
    """
    df = (date_from or "").strip() or None
    dt = (date_to or "").strip() or None
    if not df and not dt:
        return None
    if df:
        _parse_iso_day(df)
    if dt:
        _parse_iso_day(dt)
    if df and dt and df > dt:
        df, dt = dt, df

    branches: list[dict[str, Any]] = []

    scond: dict[str, Any] = {}
    if df:
        scond["$gte"] = df
    if dt:
        scond["$lte"] = dt
    if scond:
        branches.append({"report_date": scond})

    dcond: dict[str, Any] = {}
    if df:
        dcond["$gte"] = _parse_iso_day(df)
    if dt:
        end = _parse_iso_day(dt)
        end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
        dcond["$lte"] = end
    if dcond:
        branches.append({"report_date": dcond})

    if not branches:
        return None
    if len(branches) == 1:
        return branches[0]
    return {"$or": branches}


def build_alpha_daily_query(
    date_from: str | None, date_to: str | None
) -> dict[str, Any]:
    """构建 Alpha 日报 MongoDB 查询条件（不含 limit/sort）。"""
    query: dict[str, Any] = {"_schema": ALPHA_DAILY_SCHEMA}

    rd = _report_date_range_clause(date_from, date_to)
    if rd is not None:
        if "$or" in rd:
            query["$or"] = rd["$or"]
        else:
            query.update(rd)

    return query


ALPHA_DAILY_SORT: list[tuple[str, int]] = [
    ("report_date", -1),
    ("product_name", 1),
    ("_source_file", -1),
    ("_row_index", 1),
]


def fetch_alpha_daily_documents(
    *,
    limit: int = 100,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    coll = get_app_collection()
    query = build_alpha_daily_query(date_from, date_to)

    cursor = (
        coll.find(query).sort(ALPHA_DAILY_SORT).limit(max(1, min(limit, 10000)))
    )
    rows: list[dict[str, Any]] = []
    for doc in cursor:
        doc.pop("_id", None)
        rows.append(doc)
    return rows

