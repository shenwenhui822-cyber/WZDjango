"""option.volatility：股指期货相关 ETF 指标时间序列。"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from portal.db.mongo import get_option_volatility_collection

# 与 scripts/import_option_volatility/import_volatility_csv.py 中 ETF_COLUMNS 一致
ETF_METRIC_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("ETF_510050", "上证50ETF华夏"),
    ("ETF_510300", "沪深300ETF华泰柏瑞"),
    ("ETF_510500", "中证500ETF南方"),
    ("ETF_588000", "科创50ETF华夏"),
    ("ETF_588080", "科创板50ETF易方达"),
    ("ETF_159901", "深100ETF易方达"),
    ("ETF_159915", "创业板ETF易方达"),
    ("ETF_159919", "沪深300ETF嘉实"),
    ("ETF_159922", "中证500ETF嘉实"),
)

ETF_METRIC_KEYS: frozenset[str] = frozenset(k for k, _ in ETF_METRIC_DEFINITIONS)

# 数据明细表默认最多展示行数（最新在上，即最近 N 个交易日）
DEFAULT_TABLE_DISPLAY_LIMIT = 100


def _parse_date_key(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    compact = s.replace("-", "")[:8]
    if len(compact) == 8 and compact.isdigit():
        try:
            return date(
                int(compact[0:4]), int(compact[4:6]), int(compact[6:8])
            )
        except ValueError:
            return None
    return None


def _parse_metric_num(val: Any) -> float | None:
    if val is None:
        return None
    try:
        x = float(val)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def _label_for_key(key: str) -> str:
    for k, label in ETF_METRIC_DEFINITIONS:
        if k == key:
            return label
    return key


def format_metric_display(val: float | None) -> str:
    """页面展示：保留两位小数。"""
    if val is None:
        return "—"
    return f"{val:.2f}"


def parse_recent_window(raw: str | None) -> tuple[int, str]:
    """
    与基金净值 / alpha 产品表现一致：21/63/126/252 个交易日约数，all=成立以来。
    返回 (窗口长度, 原始参数)。
    """
    s = (raw or "").strip().lower()
    if s in ("21", "63", "126", "252"):
        return int(s), s
    if s == "all":
        return 0, "all"
    return 63, "63"


def slice_series_by_recent_window(
    rows: list[tuple[date, float]], recent_window: int
) -> list[tuple[date, float]]:
    """按数据点（日）数量截取最近 N 个点；recent_window<=0 表示不截断。"""
    if recent_window <= 0:
        return list(rows)
    if len(rows) <= recent_window:
        return list(rows)
    return rows[-recent_window:]


def _fetch_metric_rows(metric_key: str) -> list[tuple[date, float]]:
    key = (metric_key or "").strip()
    if key not in ETF_METRIC_KEYS:
        raise ValueError(f"未知指标: {metric_key!r}")
    coll = get_option_volatility_collection()
    proj: dict[str, int] = {"date": 1, key: 1, "_id": 0}
    rows: list[tuple[date, float]] = []
    for doc in coll.find({}, proj).sort("date", 1):
        d = _parse_date_key(doc.get("date"))
        v = _parse_metric_num(doc.get(key))
        if d is None or v is None:
            continue
        rows.append((d, v))
    return rows


def _rows_to_series_payload(
    rows: list[tuple[date, float]], *, metric_key: str
) -> dict[str, Any]:
    labels = [d.isoformat() for d, _ in rows]
    values = [round(v, 2) for _, v in rows]
    latest_val = values[-1] if values else None
    summary: dict[str, Any] = {
        "count": len(rows),
        "date_min": labels[0] if labels else "",
        "date_max": labels[-1] if labels else "",
        "latest_date": labels[-1] if labels else "",
        "latest_value": latest_val,
        "metric_key": metric_key,
        "metric_label": _label_for_key(metric_key),
    }
    return {"labels": labels, "values": values, "summary": summary}


def load_etf_volatility_series(
    *,
    metric_key: str,
    recent_window: int = 0,
    limit: int = 5000,
) -> dict[str, Any]:
    """
    读取 option.volatility，按 date 升序，返回指定 ETF 指标序列。
    """
    key = (metric_key or "").strip()
    lim = max(100, min(int(limit), 20_000))
    rows = _fetch_metric_rows(key)
    if len(rows) > lim:
        rows = rows[-lim:]
    rows = slice_series_by_recent_window(rows, recent_window)
    return _rows_to_series_payload(rows, metric_key=key)


def _fetch_all_rows_by_date(*, limit: int) -> list[tuple[date, dict[str, float]]]:
    """按 date 升序读取全部 ETF 列。"""
    lim = max(100, min(int(limit), 20_000))
    coll = get_option_volatility_collection()
    keys = [k for k, _ in ETF_METRIC_DEFINITIONS]
    proj = {"date": 1, **{k: 1 for k in keys}, "_id": 0}
    by_date: list[tuple[date, dict[str, float]]] = []
    for doc in coll.find({}, proj).sort("date", 1):
        d = _parse_date_key(doc.get("date"))
        if d is None:
            continue
        vals: dict[str, float] = {}
        for k in keys:
            v = _parse_metric_num(doc.get(k))
            if v is not None:
                vals[k] = v
        if vals:
            by_date.append((d, vals))
    if len(by_date) > lim:
        by_date = by_date[-lim:]
    return by_date


def build_etf_table_payload(
    rows_by_date: list[tuple[date, dict[str, float]]],
    *,
    table_limit: int = DEFAULT_TABLE_DISPLAY_LIMIT,
) -> dict[str, Any]:
    """将按日数据转为页面表格（日期 + 9 列 ETF，最新日期在上，默认仅展示前 table_limit 行）。"""
    keys = [k for k, _ in ETF_METRIC_DEFINITIONS]
    headers_zh = ["日期"] + [label for _, label in ETF_METRIC_DEFINITIONS]
    column_keys = ["date", *keys]
    ordered = list(reversed(rows_by_date))
    total_count = len(ordered)
    lim = max(0, int(table_limit))
    if lim > 0:
        ordered = ordered[:lim]
    table_rows: list[list[str]] = []
    for d, vals in ordered:
        table_rows.append(
            [d.isoformat()]
            + [format_metric_display(vals.get(k)) for k in keys]
        )
    return {
        "table_headers_zh": headers_zh,
        "table_column_keys": column_keys,
        "table_rows": table_rows,
        "table_row_count": len(table_rows),
        "table_total_count": total_count,
    }


def load_etf_volatility_bundle(
    *,
    metric_key: str,
    recent_window: int = 0,
    limit: int = 5000,
    table_limit: int = DEFAULT_TABLE_DISPLAY_LIMIT,
) -> dict[str, Any]:
    """
    一次读取 Mongo，返回侧栏最新值、图表序列、明细表（同一区间）。
    """
    key = (metric_key or "").strip()
    if key not in ETF_METRIC_KEYS:
        raise ValueError(f"未知指标: {metric_key!r}")

    all_rows = _fetch_all_rows_by_date(limit=limit)
    labels_all = [d.isoformat() for d, _ in all_rows]
    latest_row = all_rows[-1][1] if all_rows else {}

    window_rows = slice_series_by_recent_window(all_rows, recent_window)
    metric_rows = [
        (d, vals[key])
        for d, vals in window_rows
        if key in vals and vals[key] is not None
    ]
    series = _rows_to_series_payload(metric_rows, metric_key=key)
    table = build_etf_table_payload(window_rows, table_limit=table_limit)

    return {
        "latest_date": labels_all[-1] if labels_all else "",
        "latest_by_key": latest_row,
        "date_min": labels_all[0] if labels_all else "",
        "date_max": labels_all[-1] if labels_all else "",
        "series": series,
        **table,
    }


def load_etf_latest_snapshot(*, limit: int = 5000) -> dict[str, Any]:
    """读取全量序列，供左侧九个指标展示各自最新值。"""
    all_rows = _fetch_all_rows_by_date(limit=limit)
    labels = [d.isoformat() for d, _ in all_rows]
    latest_date = labels[-1] if labels else ""
    latest_row = all_rows[-1][1] if all_rows else {}
    return {
        "count": len(all_rows),
        "date_min": labels[0] if labels else "",
        "date_max": latest_date,
        "latest_date": latest_date,
        "latest_by_key": latest_row,
    }
