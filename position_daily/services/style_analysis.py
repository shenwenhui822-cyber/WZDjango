"""宽基风格：rq_base_index 成分 + Wind AINDEXEODPRICES 基准涨跌"""

from __future__ import annotations

import logging

import pandas as pd

from .config import BENCH_WIND_MAP, BUCKET_ORDER, MV_LARGE_WAN, MV_MID_WAN
from .mongo import rq_db
from .wind_db import _fetch_df, query_in_batches, resolve_trade_dt

logger = logging.getLogger("position_daily.style")


def _assign_bucket(row) -> str:
    for name, col in BUCKET_ORDER:
        val = row.get(col)
        if pd.notna(val) and int(val) == 1:
            return name
    return "其他"


def _mv_bucket(mv: float | None) -> str:
    if mv is None or pd.isna(mv) or mv <= 0:
        return "未知"
    if mv >= MV_LARGE_WAN:
        return "大盘"
    if mv >= MV_MID_WAN:
        return "中盘"
    return "小盘"


def _fetch_index_pct(trade_dt: str, wind_codes: list[str]) -> dict[str, float]:
    if not wind_codes:
        return {}
    ph = ",".join(["%s"] * len(wind_codes))
    df = _fetch_df(
        f"SELECT S_INFO_WINDCODE, S_DQ_PCTCHANGE FROM AINDEXEODPRICES "
        f"WHERE TRADE_DT=%s AND S_INFO_WINDCODE IN ({ph})",
        (trade_dt, *wind_codes),
    )
    return dict(zip(df["S_INFO_WINDCODE"], df["S_DQ_PCTCHANGE"].astype(float)))


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

    merged = pos_df.merge(idx_df, on="code_rq", how="left")
    for _, col in BUCKET_ORDER:
        if col not in merged.columns:
            merged[col] = 0
        else:
            merged[col] = merged[col].fillna(0)
    merged["bucket"] = merged.apply(_assign_bucket, axis=1)

    trade_dt = resolve_trade_dt("AINDEXEODPRICES", trade_date)
    quality["wind_index_trade_dt"] = trade_dt
    bench_codes = list(BENCH_WIND_MAP.values())
    index_pct = _fetch_index_pct(trade_dt, bench_codes) if trade_dt else {}

    bucket_records = []
    for bucket, g in merged.groupby("bucket"):
        bw = g["weight"].sum()
        w_chg = (g["weight"] * g["change_pct"]).sum()
        w_chg_pct = w_chg / bw if bw else 0.0
        bench = BENCH_WIND_MAP.get(bucket)
        indus_pct = index_pct.get(bench) if bench else None
        bucket_records.append(
            {
                "bucket": bucket,
                "stock_count": len(g),
                "weight": bw,
                "w_chg_pct": w_chg_pct,
                "index_pct_chg": indus_pct,
                "excess_pct": w_chg_pct - indus_pct if pd.notna(indus_pct) else None,
                "profit": g["profit"].sum(),
                "bench_code": bench,
            }
        )
    bucket_df = pd.DataFrame(bucket_records)
    if not bucket_df.empty:
        order = [b for b, _ in BUCKET_ORDER] + ["其他"]
        bucket_df["bucket"] = pd.Categorical(bucket_df["bucket"], categories=order, ordered=True)
        bucket_df = bucket_df.sort_values("bucket")

    # 市值风格（Wind 衍生指标）
    mv_df = pd.DataFrame()
    if trade_dt:
        deriv = query_in_batches(
            "ASHAREEODDERIVATIVEINDICATOR",
            ["S_INFO_WINDCODE", "S_VAL_MV"],
            pos_df["code"].dropna().unique().tolist(),
            trade_dt,
        )
        if not deriv.empty:
            m2 = pos_df.merge(deriv, left_on="code", right_on="S_INFO_WINDCODE", how="left")
            m2["mv_bucket"] = m2["S_VAL_MV"].map(_mv_bucket)
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
                        "profit": g["profit"].sum(),
                    }
                )
            mv_df = pd.DataFrame(mv_records)
            if not mv_df.empty:
                mv_order = ["大盘", "中盘", "小盘", "未知"]
                mv_df["bucket"] = pd.Categorical(mv_df["bucket"], categories=mv_order, ordered=True)
                mv_df = mv_df.sort_values("bucket")

    unmatched = merged[merged["in_HS300"].isna()] if not idx_df.empty else merged
    quality["index_unmatched"] = int(unmatched["code"].nunique()) if not unmatched.empty else 0
    quality["index_unmatched_mv_pct"] = float(unmatched["weight"].sum()) if not unmatched.empty else 0.0
    return bucket_df, mv_df, quality
