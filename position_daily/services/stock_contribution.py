"""单票对组合当日涨跌/累计盈亏贡献"""

from __future__ import annotations

import pandas as pd


def analyze_stock_contribution(
    pos_df: pd.DataFrame, top_n: int = 15
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    df = pos_df.copy()
    total_mv = float(df["market_value"].sum()) or 1.0
    df["daily_contrib"] = df["market_value"] * df["change_pct"] / 100
    df["daily_contrib_pct"] = df["daily_contrib"] / total_mv * 100
    df["contrib_to_return"] = df["weight"] * df["change_pct"]

    quality = {
        "daily_pnl": float(df["daily_contrib"].sum()),
        "daily_return_pct": float(df["contrib_to_return"].sum()),
    }

    cols = [
        "code",
        "name",
        "weight",
        "change_pct",
        "daily_contrib",
        "daily_contrib_pct",
        "profit",
        "market_value",
    ]
    top_gain = df.nlargest(top_n, "daily_contrib")[cols].copy()
    top_loss = df.nsmallest(top_n, "daily_contrib")[cols].copy()
    return top_gain, top_loss, quality
