"""将 Excel/CSV/Mongo 的各类日期写法统一为 YYYY-MM-DD 字符串。"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import pandas as pd


def trade_date_to_iso(raw: Any) -> str | None:
    """None / 空白则返回 None；支持 Timestamp、datetime、YYYYMMDD 整数、分隔符写法等。"""
    if raw is None:
        return None
    try:
        if pd.isna(raw):
            return None
    except Exception:
        pass

    if isinstance(raw, pd.Timestamp):
        return raw.strftime("%Y-%m-%d")

    if isinstance(raw, (datetime, date)):
        return raw.strftime("%Y-%m-%d")

    if isinstance(raw, (int,)) and 10_000_000 <= raw <= 99_999_999:
        s = str(raw)
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"

    if isinstance(raw, float) and raw == int(raw) and 10_000_000 <= int(raw) <= 99_999_999:
        s = str(int(raw))
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"

    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    if s.endswith(".0") and s[:-2].isdigit() and len(s) == 10:
        s = s[:-2]

    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"

    ts = pd.to_datetime(s, errors="coerce")
    if not pd.isna(ts):
        return ts.strftime("%Y-%m-%d")

    return None
