"""Excel 单元格 -> MongoDB 安全类型（空值 -> None）。"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import pandas as pd

from portal.data.enums import normalize_product_type


def _is_empty(val: Any) -> bool:
    if val is None:
        return True
    try:
        if pd.isna(val):
            return True
    except Exception:
        pass
    if isinstance(val, str) and not val.strip():
        return True
    return False


def parse_report_date(val: Any) -> str | None:
    """报表日期 -> ISO 字符串 YYYY-MM-DD（与 Excel 日期「自然日」一致，避免 BSON Date 在工具里按 UTC 显示差一天）。"""
    if _is_empty(val):
        return None
    if isinstance(val, datetime):
        dt = val.replace(tzinfo=None) if val.tzinfo else val
        return dt.strftime("%Y-%m-%d")
    if isinstance(val, date) and not isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, pd.Timestamp):
        dt = val.to_pydatetime()
        dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
        return dt.strftime("%Y-%m-%d")
    ts = pd.to_datetime(val, errors="coerce")
    if pd.isna(ts):
        return None
    dt = ts.to_pydatetime()
    dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
    return dt.strftime("%Y-%m-%d")


def parse_percentage(val: Any) -> float | None:
    """如 -1.67% -> -0.0167；Excel 中已为小数比例（|x|<=1）则不改。"""
    if _is_empty(val):
        return None
    if isinstance(val, str):
        s = val.strip()
        if not s or s in ("-", "—"):
            return None
        if "%" in s:
            s = s.replace("%", "").replace(",", "").replace("，", "").strip()
            try:
                return float(s) / 100.0
            except Exception:
                return None
        try:
            x = float(s.replace(",", ""))
        except Exception:
            return None
        if abs(x) <= 1.0:
            return x
        return x / 100.0
    try:
        import numpy as np

        if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
            return None
    except Exception:
        pass
    x = float(val)
    if abs(x) <= 1.0:
        return x
    if abs(x) <= 100.0:
        return x / 100.0
    return x / 100.0


def parse_float_amount(val: Any) -> float | None:
    """资产、金额等：去千分位逗号。"""
    if _is_empty(val):
        return None
    if isinstance(val, (int, float)):
        try:
            import numpy as np

            if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                return None
        except Exception:
            pass
        return float(val)
    s = str(val).strip()
    if not s or s in ("-", "—"):
        return None
    s = re.sub(r"[,\s]", "", s)
    s = s.replace("，", "")
    try:
        return float(s)
    except Exception:
        return None


def parse_int_optional(val: Any) -> int | None:
    if _is_empty(val):
        return None
    if isinstance(val, (int, float)):
        try:
            import numpy as np

            if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                return None
        except Exception:
            pass
        return int(round(float(val)))
    s = str(val).strip()
    if not s or s in ("-", "—"):
        return None
    s = re.sub(r"[,\s]", "", s)
    try:
        return int(round(float(s)))
    except Exception:
        return None


def parse_text(val: Any) -> str | None:
    if _is_empty(val):
        return None
    s = str(val).strip()
    return s if s else None


def parse_product_type(val: Any) -> str | None:
    return normalize_product_type(val)
