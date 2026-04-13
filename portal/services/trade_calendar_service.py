"""交易日历 CSV 导入与净值曲线数据查询。"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from django.conf import settings
from pymongo import UpdateOne

from portal.data.alpha_daily_schema import (
    ALPHA_DAILY_SCHEMA,
    is_alpha_daily_product_name_excluded,
)
from portal.db.mongo import get_app_collection, get_trade_date_collection


def import_trade_dates_csv(
    csv_path: Path | None = None, *, clear: bool = False
) -> dict[str, Any]:
    path = csv_path or settings.TRADE_DATES_CSV
    if not path.is_file():
        raise FileNotFoundError(f"未找到文件: {path}")

    df = pd.read_csv(path, dtype=str)
    if df.empty:
        return {"inserted": 0, "upserted": 0, "file": str(path)}

    col = "trade_date" if "trade_date" in df.columns else df.columns[0]
    ts = pd.to_datetime(df[col].astype(str).str.strip(), errors="coerce")
    df = df.assign(_iso=ts.dt.strftime("%Y-%m-%d"))
    df = df.dropna(subset=["_iso"])
    isos = df["_iso"].unique().tolist()

    coll = get_trade_date_collection()
    if clear:
        coll.delete_many({})

    src = path.name
    ops = [
        UpdateOne(
            {"trade_date": iso},
            {"$set": {"trade_date": iso, "source": src}},
            upsert=True,
        )
        for iso in isos
    ]
    if not ops:
        return {"inserted": 0, "upserted": 0, "file": str(path), "total_rows": 0}

    res = coll.bulk_write(ops, ordered=False)
    try:
        coll.create_index("trade_date", unique=True)
    except Exception:
        pass

    return {
        "file": str(path),
        "total_rows": len(isos),
        "matched": res.matched_count,
        "modified": res.modified_count,
        "upserted": res.upserted_count,
    }


def trading_date_iso_set() -> frozenset[str]:
    coll = get_trade_date_collection()
    return frozenset(
        d["trade_date"]
        for d in coll.find({}, {"trade_date": 1, "_id": 0})
        if d.get("trade_date")
    )


def is_trade_date_iso(iso: str) -> bool:
    """判断 YYYY-MM-DD 是否在 MongoDB trade_calendar 中。"""
    day = (iso or "").strip()[:10]
    if len(day) != 10:
        return False
    coll = get_trade_date_collection()
    return coll.find_one({"trade_date": day}, {"_id": 1}) is not None


def prev_trading_day_iso_before(ref_iso: str, *, max_days: int = 400) -> str | None:
    """
    严格早于 ref_iso（通常为「运行日」当日）的最近一个交易日 YYYY-MM-DD。
    从 ref 的前一日起逐日往前，直到命中 trade_calendar 或超出 max_days。
    """
    day = (ref_iso or "").strip()[:10]
    if len(day) != 10:
        return None
    ref = date.fromisoformat(day)
    d = ref - timedelta(days=1)
    for _ in range(max_days):
        if is_trade_date_iso(d.isoformat()):
            return d.isoformat()
        d -= timedelta(days=1)
    return None


def distinct_product_names() -> list[str]:
    coll = get_app_collection()
    names = coll.distinct(
        "product_name", {"_schema": ALPHA_DAILY_SCHEMA, "product_name": {"$ne": None}}
    )
    return sorted(
        n
        for n in (str(x) for x in names if x)
        if not is_alpha_daily_product_name_excluded(n)
    )


def _iso_day(v: Any) -> str | None:
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    return s[:10] if len(s) >= 10 else s


def fetch_nav_curve_series(
    *,
    product_name: str,
    date_from: str | None,
    date_to: str | None,
    only_trading_days: bool,
    recent_trading_days: int | None = None,
) -> list[dict[str, Any]]:
    pn = (product_name or "").strip()
    if not pn:
        raise ValueError("请选择产品名称")
    if is_alpha_daily_product_name_excluded(pn):
        raise ValueError("该产品不在展示范围内")

    coll = get_app_collection()

    if recent_trading_days in (5, 10, 15):
        q0: dict[str, Any] = {
            "_schema": ALPHA_DAILY_SCHEMA,
            "product_name": pn,
            "current_nav": {"$ne": None},
        }
        dates: list[str] = []
        for d in coll.find(q0, {"report_date": 1}):
            day = _iso_day(d.get("report_date"))
            if day:
                dates.append(day)
        dates = sorted(set(dates))
        if only_trading_days:
            tset = trading_date_iso_set()
            if tset:
                dates = [x for x in dates if x in tset]
        if len(dates) > recent_trading_days:
            dates = dates[-recent_trading_days:]
        if not dates:
            return []
        q1 = {
            "_schema": ALPHA_DAILY_SCHEMA,
            "product_name": pn,
            "report_date": {"$in": dates},
        }
        rows = list(
            coll.find(
                q1,
                {"_id": 0, "report_date": 1, "product_name": 1, "current_nav": 1},
            ).sort("report_date", 1)
        )
    else:
        q: dict[str, Any] = {"_schema": ALPHA_DAILY_SCHEMA, "product_name": pn}
        rd: dict[str, Any] = {}
        if date_from:
            rd["$gte"] = date_from
        if date_to:
            rd["$lte"] = date_to
        if rd:
            q["report_date"] = rd

        rows = list(
            coll.find(
                q,
                {"_id": 0, "report_date": 1, "product_name": 1, "current_nav": 1},
            ).sort("report_date", 1)
        )
        if only_trading_days:
            tset = trading_date_iso_set()
            if tset:
                rows = [r for r in rows if _iso_day(r.get("report_date")) in tset]

    out: list[dict[str, Any]] = []
    for r in rows:
        nav = r.get("current_nav")
        if nav is None:
            continue
        rd = _iso_day(r.get("report_date"))
        if rd is None:
            continue
        out.append(
            {
                "report_date": rd,
                "product_name": r.get("product_name"),
                "current_nav": float(nav),
            }
        )
    return out

