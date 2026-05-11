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


def qichat_prev_iso_week_trading_ymd_pair(ref_iso: str) -> tuple[str, str] | None:
    """
    以 ref_iso（通常为运行日）为基准，取「上一自然周（周一至周日）」内最早与最晚交易日，
    组成主题中的两段日期 YYYYMMDD，例如 吾执_周度绩效_20260420_20260424。
    """
    day = (ref_iso or "").strip()[:10]
    if len(day) != 10:
        return None
    ref = date.fromisoformat(day)
    this_monday = ref - timedelta(days=ref.weekday())
    prev_week_monday = this_monday - timedelta(days=7)
    prev_week_sunday = this_monday - timedelta(days=1)
    trading_days: list[date] = []
    cur = prev_week_monday
    while cur <= prev_week_sunday:
        if is_trade_date_iso(cur.isoformat()):
            trading_days.append(cur)
        cur += timedelta(days=1)
    if not trading_days:
        return None
    return (
        trading_days[0].strftime("%Y%m%d"),
        trading_days[-1].strftime("%Y%m%d"),
    )


def is_first_trading_day_of_iso_week(iso: str) -> bool:
    """
    是否为当前自然周（周一至周日）内的首个交易日。
    若周一为非交易日，则本周首个交易日为周二等顺延日 —— 仅在这些日期返回 True。
    """
    day = (iso or "").strip()[:10]
    if len(day) != 10:
        return False
    if not is_trade_date_iso(day):
        return False
    today = date.fromisoformat(day)
    monday = today - timedelta(days=today.weekday())
    d = monday
    while d < today:
        if is_trade_date_iso(d.isoformat()):
            return False
        d += timedelta(days=1)
    return True


def calendar_day_isos_prev_trading_through_run(run_iso: str) -> list[str]:
    """
    从前一交易日（T-1）到 run_iso（通常为运行日「今天」）闭区间内的**每一个**自然日 YYYY-MM-DD，
    顺序为**新→旧**。中间凡是非交易日（周末、长假等）**一律包含**，不做省略或截断。
    """
    day = (run_iso or "").strip()[:10]
    if len(day) != 10:
        return []
    end = date.fromisoformat(day)
    prev_t = prev_trading_day_iso_before(run_iso)
    if not prev_t:
        return [end.isoformat()]
    start = date.fromisoformat(prev_t[:10])
    if end < start:
        return [end.isoformat()]
    out: list[str] = []
    d = end
    while d >= start:
        out.append(d.isoformat())
        d -= timedelta(days=1)
    return out


def last_n_prev_trading_day_isos(ref_iso: str, n: int, *, max_days: int = 400) -> list[str]:
    """
    从 ref_iso 的前一交易日起，连续向前取最多 n 个交易日 YYYY-MM-DD。
    例：ref 为运行日，则依次为 T-1、T-2、T-3（均为交易日）。

    若需「T-1 至今天」之间每个自然日（含非交易日），请用 calendar_day_isos_prev_trading_through_run。
    """
    out: list[str] = []
    ref = (ref_iso or "").strip()[:10]
    if len(ref) != 10:
        return out
    for _ in range(max(0, n)):
        p = prev_trading_day_iso_before(ref, max_days=max_days)
        if not p:
            break
        out.append(p)
        ref = p
    return out


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


def count_trading_days_inclusive(start_iso: str, end_iso: str) -> int:
    """闭区间 [start_iso, end_iso] 内（含首尾）的交易日数量；要求 start_iso <= end_iso。"""
    s = (start_iso or "").strip()[:10]
    e = (end_iso or "").strip()[:10]
    if len(s) != 10 or len(e) != 10:
        return 0
    d0 = date.fromisoformat(s)
    d1 = date.fromisoformat(e)
    if d0 > d1:
        return 0
    n = 0
    cur = d0
    one = timedelta(days=1)
    while cur <= d1:
        if is_trade_date_iso(cur.isoformat()):
            n += 1
        cur += one
    return n


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

    if recent_trading_days is not None:
        if recent_trading_days < 0:
            raise ValueError("recent_trading_days 不能小于 0")
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
        # 0 表示“成立以来”（不截断）；>0 表示取最近 N 个交易日
        if recent_trading_days > 0 and len(dates) > recent_trading_days:
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

