"""单票对组合当日涨跌/累计盈亏贡献"""

from __future__ import annotations

import pandas as pd


def _safe_float(value, default: float = 0.0) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    return int(_safe_float(value, default))


def change_amount_of(row) -> float:
    """单票相对昨收的涨跌额（元）。"""
    amt = _safe_float(row.get("change_amount") if hasattr(row, "get") else None, default=float("nan"))
    if amt == amt and amt != 0:
        return amt
    last_price = _safe_float(row.get("last_price") if hasattr(row, "get") else row["last_price"])
    prev_close = _safe_float(row.get("prev_close") if hasattr(row, "get") else row["prev_close"])
    if last_price > 0 and prev_close > 0:
        return last_price - prev_close
    change_pct = _safe_float(row.get("change_pct") if hasattr(row, "get") else row.get("change_pct"))
    if change_pct != 0 and prev_close > 0:
        return prev_close * change_pct / 100
    return 0.0


def calc_row_daily_pnl(row, *, prev_volume: int | None = None) -> float:
    """
    单票当日浮动盈亏（元）。

    - 隔夜持仓：carry_volume × change_amount
    - 当日加仓：new_volume × (last_price - cost_price)
    - 当日减仓（含清仓）：sold_volume × change_amount（按昨收至现价近似）
    - 无上一日快照时：volume × change_amount
    """
    volume = _safe_int(row.get("volume") if hasattr(row, "get") else row["volume"])
    if volume <= 0:
        return 0.0

    change_amt = change_amount_of(row)
    last_price = _safe_float(row.get("last_price") if hasattr(row, "get") else row["last_price"])
    cost_price = _safe_float(row.get("cost_price") if hasattr(row, "get") else row.get("cost_price"))

    if prev_volume is None:
        return volume * change_amt

    if prev_volume <= 0:
        if cost_price > 0 and last_price > 0:
            return volume * (last_price - cost_price)
        return volume * change_amt

    carry_volume = min(volume, prev_volume)
    pnl = carry_volume * change_amt

    new_volume = volume - prev_volume
    if new_volume > 0:
        if cost_price > 0 and last_price > 0:
            pnl += new_volume * (last_price - cost_price)
        else:
            pnl += new_volume * change_amt

    sold_volume = prev_volume - volume
    if sold_volume > 0:
        pnl += sold_volume * change_amt

    return pnl


def _prev_volume_map(prev_df: pd.DataFrame | None) -> dict[str, int]:
    if prev_df is None or prev_df.empty or "code" not in prev_df.columns:
        return {}
    return {str(c): _safe_int(v) for c, v in zip(prev_df["code"], prev_df["volume"])}


def _trade_note(volume: int, prev_volume: int | None) -> str:
    if prev_volume is None:
        return "hold"
    if prev_volume <= 0:
        return "new"
    if volume > prev_volume:
        return "buy"
    if volume < prev_volume:
        return "sell"
    return "hold"


def enrich_positions(pos_df: pd.DataFrame, prev_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """为持仓附加 prev_volume / daily_contrib / trade_note。"""
    df = pos_df.copy()
    prev_map = _prev_volume_map(prev_df)
    has_prev = prev_df is not None

    prev_volumes: list[int | None] = []
    daily_contribs: list[float] = []
    trade_notes: list[str] = []

    for _, row in df.iterrows():
        code = str(row["code"])
        vol = _safe_int(row["volume"])
        prev_vol = prev_map.get(code, 0) if has_prev else None
        prev_volumes.append(prev_vol if has_prev else None)
        daily_contribs.append(calc_row_daily_pnl(row, prev_volume=prev_vol))
        trade_notes.append(_trade_note(vol, prev_vol if has_prev else None))

    df["prev_volume"] = prev_volumes
    df["daily_contrib"] = daily_contribs
    df["trade_note"] = trade_notes

    total_mv = float(df["market_value"].sum()) or 1.0
    df["daily_contrib_pct"] = df["daily_contrib"] / total_mv * 100
    if "weight" in df.columns and "change_pct" in df.columns:
        df["contrib_to_return"] = df["weight"] * df["change_pct"]
    return df


def calc_daily_pnl(df: pd.DataFrame, prev_df: pd.DataFrame | None = None) -> pd.Series:
    """当日浮动盈亏（元）序列；优先使用已 enrich 的 daily_contrib。"""
    if "daily_contrib" in df.columns:
        return df["daily_contrib"]
    return enrich_positions(df, prev_df)["daily_contrib"]


def sum_daily_pnl(g: pd.DataFrame, prev_df: pd.DataFrame | None = None) -> float:
    return float(calc_daily_pnl(g, prev_df).sum())


def analyze_stock_contribution(
    pos_df: pd.DataFrame, top_n: int = 15, *, prev_df: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    df = enrich_positions(pos_df, prev_df) if "daily_contrib" not in pos_df.columns else pos_df.copy()
    total_mv = float(df["market_value"].sum()) or 1.0
    if "daily_contrib_pct" not in df.columns:
        df["daily_contrib_pct"] = df["daily_contrib"] / total_mv * 100
    if "contrib_to_return" not in df.columns:
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


ALL_STOCK_COLUMNS = [
    ("code", "代码"),
    ("name", "名称"),
    ("volume", "持仓"),
    ("available_volume", "可用"),
    ("prev_volume", "昨仓"),
    ("trade_note", "变动"),
    ("weight", "权重"),
    ("last_price", "现价"),
    ("prev_close", "昨收"),
    ("change_pct", "涨跌"),
    ("daily_contrib", "当日盈亏"),
    ("profit", "累计盈亏"),
    ("market_value", "市值"),
]

TRADE_NOTE_LABELS = {"hold": "持仓", "new": "新进", "buy": "加仓", "sell": "减仓"}


def build_all_stock_holdings(
    pos_df: pd.DataFrame, *, prev_df: pd.DataFrame | None = None
) -> tuple[list[dict], list[tuple[str, str]], int]:
    """全部持股明细，默认按权重降序。"""
    df = enrich_positions(pos_df, prev_df) if "daily_contrib" not in pos_df.columns else pos_df.copy()
    df = df.sort_values("market_value", ascending=False)

    keys = [k for k, _ in ALL_STOCK_COLUMNS]
    rows = []
    for _, r in df.iterrows():
        display: dict = {}
        for key, _label in ALL_STOCK_COLUMNS:
            v = r.get(key)
            if key == "trade_note":
                display[key] = TRADE_NOTE_LABELS.get(str(v), str(v))
            elif key == "weight" and pd.notna(v):
                display[key] = f"{float(v) * 100:.2f}%"
            elif key == "change_pct" and pd.notna(v):
                display[key] = f"{float(v):.2f}%"
            elif key in ("daily_contrib", "profit", "market_value", "last_price", "prev_close") and pd.notna(v):
                display[key] = round(float(v), 2)
            elif key in ("volume", "available_volume", "prev_volume"):
                display[key] = int(v) if pd.notna(v) else "—"
            elif pd.isna(v):
                display[key] = "—"
            else:
                display[key] = v

        sort: dict = {}
        for key in keys:
            val = r.get(key)
            if key in ("code", "name", "trade_note"):
                sort[key] = str(val) if pd.notna(val) else ""
            elif key == "weight":
                sort[key] = float(val) if pd.notna(val) else 0.0
            elif key in ("volume", "available_volume", "prev_volume"):
                sort[key] = int(val) if pd.notna(val) else 0
            else:
                sort[key] = float(val) if pd.notna(val) else 0.0
        rows.append({"display": display, "sort": sort, "default_sort": "weight"})
    return rows, ALL_STOCK_COLUMNS, len(df)
