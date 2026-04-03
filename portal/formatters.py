"""Alpha 日报：Mongo 原始值 -> 页面展示字符串。"""
from __future__ import annotations

from typing import Any

# 按百分比展示的英文字段（库内存为小数比例）
_RATIO_KEYS = frozenset(
    {
        "daily_pnl_ratio",
        "weekly_pnl_ratio",
        "monthly_pnl_ratio",
        "yearly_pnl_ratio",
        "daily_excess_return_ratio",
        "weekly_excess_return_ratio",
        "monthly_excess_return_ratio",
        "yearly_excess_return_ratio",
        "current_nav_drawdown",
        "max_nav_drawdown",
        "current_excess_nav_drawdown",
        "max_excess_nav_drawdown",
        "long_short_exposure_ratio",
        "turnover_rate",
    }
)

_FLOAT_KEYS = frozenset(
    {
        "current_nav",
        "current_excess_nav",
        "trading_slippage",
        "total_assets",
        "total_pnl_amount",
        "long_position_mkt_value",
        "short_position_mkt_value",
        "long_account_cash",
    }
)


def format_alpha_cell(en_key: str, val: Any) -> str:
    if val is None:
        return "—"
    if en_key == "report_date":
        if hasattr(val, "strftime"):
            return val.strftime("%Y-%m-%d")
        return str(val)[:10]
    if en_key in _RATIO_KEYS:
        try:
            x = float(val)
            return f"{x * 100:.4f}%"
        except (TypeError, ValueError):
            return str(val)
    if en_key in _FLOAT_KEYS:
        try:
            x = float(val)
            return f"{x:,.4f}"
        except (TypeError, ValueError):
            return str(val)
    if en_key in ("product_name", "product_type"):
        return str(val)
    return str(val)


def row_to_display_cells(
    doc: dict, field_keys: list[str]
) -> list[str]:
    return [format_alpha_cell(k, doc.get(k)) for k in field_keys]
