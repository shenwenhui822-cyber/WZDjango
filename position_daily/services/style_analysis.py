"""宽基风格：rq_base_index 成分 + 通联 mkt_idxd 基准涨跌 + mkt_equd_eval 市值分桶"""

from __future__ import annotations

import logging

import pandas as pd

from .config import BENCH_INDEX_MAP, BUCKET_ORDER, MV_LARGE_YUAN, MV_MID_YUAN
from .mongo import rq_db
from .stock_contribution import sum_daily_pnl
from .wind_db import (
    chg_pct_to_percent,
    resolve_trade_dt,
    _fetch_df,
)
from .wind_analysis import _fetch_eval


logger = logging.getLogger("position_daily.style")

# 兼容旧名
BENCH_WIND_MAP = BENCH_INDEX_MAP


def _assign_bucket(row) -> str:
    for name, col in BUCKET_ORDER:
        val = row.get(col)
        if pd.notna(val) and int(val) == 1:
            return name
    return "其他"


def _mv_bucket(mv: float | None) -> str:
    """市值单位：元。"""
    if mv is None or pd.isna(mv) or mv <= 0:
        return "未知"
    if mv >= MV_LARGE_YUAN:
        return "大盘"
    if mv >= MV_MID_YUAN:
        return "中盘"
    return "小盘"


def _fetch_index_pct(trade_dt: str, tickers: list[str]) -> dict[str, float]:
    if not tickers or not trade_dt:
        return {}
    ph = ",".join(["%s"] * len(tickers))
    df = _fetch_df(
        f"SELECT TICKER_SYMBOL, CHG_PCT FROM mkt_idxd "
        f"WHERE TRADE_DATE=%s AND TICKER_SYMBOL IN ({ph})",
        (trade_dt, *tickers),
    )
    if df.empty:
        return {}
    return dict(zip(df["TICKER_SYMBOL"].astype(str), chg_pct_to_percent(df["CHG_PCT"])))


def analyze_style(pos_df: pd.DataFrame, trade_date: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """返回 (宽基 bucket_df, 市值风格 mv_df, quality)。"""
    quality: dict = {}
    code_list = pos_df["code_rq"].dropna().unique().tolist()
    idx_rows = list(
        rq_db()["rq_base_index"].find(
            {"date": trade_date, "code_rq": {"$in": code_list}},
            {"_id": 0},
        )
    )
    idx_df = pd.DataFrame(idx_rows)
    logger.info("rq_base_index 返回 %d 条 date=%s", len(idx_df), trade_date)

    if idx_df.empty or "code_rq" not in idx_df.columns:
        merged = pos_df.copy()
        for _, col in BUCKET_ORDER:
            merged[col] = 0
    else:
        merged = pos_df.merge(idx_df, on="code_rq", how="left")
        for _, col in BUCKET_ORDER:
            if col not in merged.columns:
                merged[col] = 0
            else:
                merged[col] = merged[col].fillna(0)
    merged["bucket"] = merged.apply(_assign_bucket, axis=1)

    trade_dt = resolve_trade_dt("mkt_idxd", trade_date)
    quality["wind_index_trade_dt"] = trade_dt.replace("-", "") if trade_dt else None
    bench_codes = list(BENCH_INDEX_MAP.values())
    index_pct = _fetch_index_pct(trade_dt, bench_codes) if trade_dt else {}

    bucket_records = []
    for bucket, g in merged.groupby("bucket"):
        bw = g["weight"].sum()
        w_chg = (g["weight"] * g["change_pct"]).sum()
        w_chg_pct = w_chg / bw if bw else 0.0
        bench = BENCH_INDEX_MAP.get(bucket)
        indus_pct = index_pct.get(bench) if bench else None
        bucket_records.append(
            {
                "bucket": bucket,
                "stock_count": len(g),
                "weight": bw,
                "w_chg_pct": w_chg_pct,
                "index_pct_chg": indus_pct,
                "excess_pct": (w_chg_pct - indus_pct) if pd.notna(indus_pct) else float("nan"),
                "daily_pnl": sum_daily_pnl(g),
                "profit": g["profit"].sum(),
                "bench_code": bench,
            }
        )
    bucket_df = pd.DataFrame(bucket_records)
    if not bucket_df.empty:
        order = [b for b, _ in BUCKET_ORDER] + ["其他"]
        bucket_df["bucket"] = pd.Categorical(bucket_df["bucket"], categories=order, ordered=True)
        bucket_df = bucket_df.sort_values("bucket")

    # 市值风格：复用估值阶段的 mkt_equd_eval 缓存
    mv_df = pd.DataFrame()
    if trade_dt:
        deriv = _fetch_eval(pos_df, trade_dt)
        if not deriv.empty and "MARKET_VALUE" in deriv.columns:
            m2 = pos_df.merge(deriv[["code", "MARKET_VALUE"]], on="code", how="left")
            m2["mv_bucket"] = m2["MARKET_VALUE"].map(_mv_bucket)
            mv_records = []
            for mb, g in m2.groupby("mv_bucket"):
                bw = g["weight"].sum()
                w_chg = (g["weight"] * g["change_pct"]).sum()
                mv_records.append(
                    {
                        "bucket": mb,
                        "stock_count": len(g),
                        "weight": bw,
                        "w_chg_pct": w_chg / bw if bw else 0.0,
                        "daily_pnl": sum_daily_pnl(g),
                        "profit": g["profit"].sum(),
                    }
                )
            mv_df = pd.DataFrame(mv_records)
            if not mv_df.empty:
                mv_order = ["大盘", "中盘", "小盘", "未知"]
                mv_df["bucket"] = pd.Categorical(mv_df["bucket"], categories=mv_order, ordered=True)
                mv_df = mv_df.sort_values("bucket")

    unmatched = merged
    if not idx_df.empty and "in_HS300" in merged.columns:
        unmatched = merged[merged["in_HS300"].isna()]
    quality["index_unmatched"] = int(unmatched["code"].nunique()) if not unmatched.empty else 0
    quality["index_unmatched_mv_pct"] = float(unmatched["weight"].sum()) if not unmatched.empty else 0.0
    return bucket_df, mv_df, quality
