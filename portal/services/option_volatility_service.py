"""option.volatility：波动率时间序列。"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from portal.db.mongo import get_option_volatility_collection


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


def _parse_volatility_num(val: Any) -> float | None:
    if val is None:
        return None
    try:
        x = float(val)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x


def load_volatility_series(*, limit: int = 5000) -> dict[str, Any]:
    """
    读取 option.volatility，按 date 升序。
    返回 labels（YYYY-MM-DD）、values、summary（最新值与区间）。
    """
    lim = max(100, min(int(limit), 20_000))
    coll = get_option_volatility_collection()
    rows: list[tuple[date, float]] = []
    for doc in coll.find(
        {}, {"date": 1, "volatility_num": 1, "_id": 0}
    ).sort("date", 1):
        d = _parse_date_key(doc.get("date"))
        v = _parse_volatility_num(doc.get("volatility_num"))
        if d is None or v is None:
            continue
        rows.append((d, v))
    if len(rows) > lim:
        rows = rows[-lim:]
    labels = [d.isoformat() for d, _ in rows]
    values = [v for _, v in rows]
    summary: dict[str, Any] = {
        "count": len(rows),
        "date_min": labels[0] if labels else "",
        "date_max": labels[-1] if labels else "",
        "latest_date": labels[-1] if labels else "",
        "latest_value": values[-1] if values else None,
    }
    return {
        "labels": labels,
        "values": values,
        "summary": summary,
    }
