"""报告数字展示格式。"""

from __future__ import annotations


def format_money(value, *, digits: int = 2) -> str:
    """金额千分位，如 9,795,273.39。"""
    if value is None:
        return "—"
    try:
        num = float(value)
        if num != num:  # NaN
            return "—"
    except (TypeError, ValueError):
        return "—"
    return f"{num:,.{digits}f}"
