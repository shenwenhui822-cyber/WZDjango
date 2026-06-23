"""Wind 维度分析：估值、资金流、一致预期、中信行业、主题"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .stock_contribution import sum_daily_pnl
from .wind_db import (
    BATCH_SIZE,
    _fetch_df,
    citics_to_level2,
    from_trade_dt,
    load_citics_l2_index_map,
    load_industry_name_map,
    query_in_batches,
    resolve_trade_dt,
    theme_code_to_industries,
    to_trade_dt,
)

logger = logging.getLogger("position_daily.wind")


def _weighted_avg(values: pd.Series, weights: pd.Series) -> float | None:
    mask = values.notna() & weights.notna() & (weights > 0)
    if not mask.any():
        return None
    w = weights[mask]
    v = values[mask].astype(float)
    return float((w * v).sum() / w.sum())


def analyze_valuation(pos_df: pd.DataFrame, trade_date: str) -> tuple[dict, dict]:
    """组合加权 PE/PB/市值及全市场市值分位。"""
    codes = pos_df["code"].dropna().unique().tolist()
    trade_dt = resolve_trade_dt("ASHAREEODDERIVATIVEINDICATOR", trade_date)
    quality = {"wind_deriv_trade_dt": trade_dt, "wind_deriv_date": from_trade_dt(trade_dt) if trade_dt else None}
    if not trade_dt:
        return {}, quality

    deriv = query_in_batches(
        "ASHAREEODDERIVATIVEINDICATOR",
        ["S_INFO_WINDCODE", "S_VAL_PE_TTM", "S_VAL_PB_NEW", "S_VAL_MV"],
        codes,
        trade_dt,
    )
    if deriv.empty:
        quality["deriv_matched"] = 0
        return {}, quality

    merged = pos_df.merge(deriv, left_on="code", right_on="S_INFO_WINDCODE", how="left")
    matched = merged[merged["S_VAL_MV"].notna()]
    quality["deriv_matched"] = int(matched["code"].nunique())
    quality["deriv_coverage"] = float(matched["weight"].sum())

    # 全 A 市值分位（截面）
    mv_pct_map = {}
    try:
        mv_all = _fetch_df(
            "SELECT S_INFO_WINDCODE, S_VAL_MV FROM ASHAREEODDERIVATIVEINDICATOR "
            "WHERE TRADE_DT=%s AND S_VAL_MV IS NOT NULL AND S_VAL_MV > 0",
            (trade_dt,),
        )
        if not mv_all.empty:
            mv_all["pct_rank"] = mv_all["S_VAL_MV"].rank(pct=True) * 100
            mv_pct_map = dict(zip(mv_all["S_INFO_WINDCODE"], mv_all["pct_rank"]))
    except Exception:
        logger.exception("全市场市值分位查询失败")

    matched = matched.copy()
    matched["mv_pct"] = matched["code"].map(mv_pct_map)

    summary = {
        "w_pe_ttm": _weighted_avg(matched["S_VAL_PE_TTM"], matched["weight"]),
        "w_pb": _weighted_avg(matched["S_VAL_PB_NEW"], matched["weight"]),
        "w_mv_yi": _weighted_avg(matched["S_VAL_MV"] / 10000, matched["weight"]),
        "w_mv_pct": _weighted_avg(matched["mv_pct"], matched["weight"]),
        "median_pe_ttm": float(matched["S_VAL_PE_TTM"].median()) if matched["S_VAL_PE_TTM"].notna().any() else None,
        "median_pb": float(matched["S_VAL_PB_NEW"].median()) if matched["S_VAL_PB_NEW"].notna().any() else None,
    }
    return summary, quality


def analyze_moneyflow(pos_df: pd.DataFrame, trade_date: str) -> tuple[dict, dict]:
    trade_dt = resolve_trade_dt("ASHAREMONEYFLOW", trade_date)
    quality = {"wind_mf_trade_dt": trade_dt}
    if not trade_dt:
        return {}, quality

    codes = pos_df["code"].dropna().unique().tolist()
    mf = query_in_batches(
        "ASHAREMONEYFLOW",
        [
            "S_INFO_WINDCODE",
            "BUY_VALUE_EXLARGE_ORDER",
            "SELL_VALUE_EXLARGE_ORDER",
            "BUY_VALUE_LARGE_ORDER",
            "SELL_VALUE_LARGE_ORDER",
        ],
        codes,
        trade_dt,
    )
    if mf.empty:
        quality["mf_matched"] = 0
        return {}, quality

    mf["net_exlarge"] = mf["BUY_VALUE_EXLARGE_ORDER"].astype(float) - mf["SELL_VALUE_EXLARGE_ORDER"].astype(float)
    mf["net_large"] = mf["BUY_VALUE_LARGE_ORDER"].astype(float) - mf["SELL_VALUE_LARGE_ORDER"].astype(float)

    merged = pos_df.merge(mf, left_on="code", right_on="S_INFO_WINDCODE", how="left")
    matched = merged[merged["net_exlarge"].notna()]
    quality["mf_matched"] = int(matched["code"].nunique())
    quality["mf_coverage"] = float(matched["weight"].sum())

    total_mv = float(pos_df["market_value"].sum()) or 1.0
    net_ex = float(matched["net_exlarge"].sum())
    net_lg = float(matched["net_large"].sum())

    # 按权重汇总的净流入强度（占组合市值比例）
    matched = matched.copy()
    matched["net_exlarge_rate"] = np.where(
        matched["market_value"] > 0,
        matched["net_exlarge"] / matched["market_value"] * 100,
        np.nan,
    )
    matched["net_large_rate"] = np.where(
        matched["market_value"] > 0,
        matched["net_large"] / matched["market_value"] * 100,
        np.nan,
    )

    summary = {
        "total_net_exlarge": net_ex,
        "total_net_large": net_lg,
        "total_net_exlarge_pct_mv": net_ex / total_mv * 100,
        "w_net_exlarge_rate": _weighted_avg(matched["net_exlarge_rate"], matched["weight"]),
        "w_net_large_rate": _weighted_avg(matched["net_large_rate"], matched["weight"]),
        "signal": "净流入" if net_ex > 0 else ("净流出" if net_ex < 0 else "中性"),
    }
    return summary, quality


def analyze_consensus(pos_df: pd.DataFrame, trade_date: str) -> tuple[dict, dict]:
    est_dt = resolve_trade_dt("ASHARECONSENSUSROLLINGDATA", trade_date, date_col="EST_DT")
    quality = {"wind_consensus_est_dt": est_dt}
    if not est_dt:
        return {}, quality

    codes = pos_df["code"].dropna().unique().tolist()
    cur = query_in_batches(
        "ASHARECONSENSUSROLLINGDATA",
        ["S_INFO_WINDCODE", "NET_PROFIT", "EST_PE", "EST_EPS"],
        codes,
        est_dt,
        date_col="EST_DT",
        extra_where="ROLLING_TYPE=%s",
        extra_params=("FY1",),
    )
    quality["consensus_matched"] = int(cur["S_INFO_WINDCODE"].nunique()) if not cur.empty else 0
    quality["consensus_coverage"] = float(
        pos_df[pos_df["code"].isin(cur["S_INFO_WINDCODE"])]["weight"].sum()
    ) if not cur.empty else 0.0

    # 前一 EST_DT：用样本股快速取最近两个预测日，避免全表 MAX 扫描
    prev_dt = None
    try:
        ladder = _fetch_df(
            "SELECT DISTINCT EST_DT FROM ASHARECONSENSUSROLLINGDATA "
            "WHERE S_INFO_WINDCODE='600028.SH' AND ROLLING_TYPE='FY1' AND EST_DT <= %s "
            "ORDER BY EST_DT DESC LIMIT 2",
            (est_dt,),
        )
        if len(ladder) >= 2:
            prev_dt = str(ladder.iloc[1]["EST_DT"])
    except Exception:
        logger.warning("一致预期前一 EST_DT 查询失败，跳过净利润变化")
    quality["consensus_prev_est_dt"] = prev_dt

    prev = pd.DataFrame()
    if prev_dt:
        prev = query_in_batches(
            "ASHARECONSENSUSROLLINGDATA",
            ["S_INFO_WINDCODE", "NET_PROFIT"],
            codes,
            prev_dt,
            date_col="EST_DT",
            extra_where="ROLLING_TYPE=%s",
            extra_params=("FY1",),
        )
        if not prev.empty:
            prev = prev.rename(columns={"NET_PROFIT": "NET_PROFIT_PREV"})

    merged = pos_df.merge(cur, left_on="code", right_on="S_INFO_WINDCODE", how="left")
    if not prev.empty:
        merged = merged.merge(prev, left_on="code", right_on="S_INFO_WINDCODE", how="left", suffixes=("", "_p"))

    matched = merged[merged["EST_PE"].notna()].copy()
    if not matched.empty and "NET_PROFIT_PREV" in matched.columns:
        matched["np_chg_pct"] = np.where(
            matched["NET_PROFIT_PREV"].astype(float).abs() > 0,
            (matched["NET_PROFIT"].astype(float) - matched["NET_PROFIT_PREV"].astype(float))
            / matched["NET_PROFIT_PREV"].astype(float).abs()
            * 100,
            np.nan,
        )

    summary = {
        "w_est_pe": _weighted_avg(matched["EST_PE"], matched["weight"]) if not matched.empty else None,
        "w_net_profit_yi": _weighted_avg(matched["NET_PROFIT"] / 1e8, matched["weight"]) if not matched.empty else None,
        "w_np_chg_pct": _weighted_avg(matched.get("np_chg_pct", pd.Series(dtype=float)), matched["weight"])
        if not matched.empty and "np_chg_pct" in matched.columns
        else None,
        "coverage_count": quality["consensus_matched"],
        "coverage_pct": quality["consensus_coverage"],
    }
    return summary, quality


def _build_industry_style_df(
    pos_df: pd.DataFrame,
    stock_map: pd.DataFrame,
    index_pct: dict[str, float],
    code_name_col: str = "indus_code",
) -> pd.DataFrame:
    merged = pos_df.merge(stock_map, on="code", how="left")
    unmapped = merged[merged[code_name_col].isna()]
    mapped = merged.dropna(subset=[code_name_col]).copy()

    records = []
    name_col = "indus_name"
    for (icode, iname), g in mapped.groupby([code_name_col, name_col]):
        indus_weight = g["weight"].sum()
        w_chg = (g["weight"] * g["change_pct"]).sum()
        w_chg_pct = w_chg / indus_weight if indus_weight else 0.0
        idx_code = g["index_code"].iloc[0] if "index_code" in g.columns else None
        indus_pct = index_pct.get(idx_code) if idx_code else None
        records.append(
            {
                "indus_code": icode,
                "indus_name": iname,
                "stock_count": len(g),
                "weight": indus_weight,
                "w_chg_pct": w_chg_pct,
                "indus_pct_chg": indus_pct,
                "excess_pct": w_chg_pct - indus_pct if pd.notna(indus_pct) else None,
                "daily_pnl": sum_daily_pnl(g),
                "profit": g["profit"].sum(),
            }
        )
    df = pd.DataFrame(records)
    if not df.empty:
        df = df.sort_values("weight", ascending=False)
    return df, unmapped


def analyze_citics_industry(pos_df: pd.DataFrame, trade_date: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    trade_dt = resolve_trade_dt("AINDEXINDUSTRIESEODCITICS", trade_date)
    quality = {"wind_citics_trade_dt": trade_dt}
    codes = pos_df["code"].dropna().unique().tolist()

    # 股票 → 中信行业（当前有效）
    ph_parts = []
    params: list = []
    for i in range(0, len(codes), BATCH_SIZE):
        batch = codes[i : i + BATCH_SIZE]
        ph = ",".join(["%s"] * len(batch))
        ph_parts.append(f"S_INFO_WINDCODE IN ({ph})")
        params.extend(batch)
    where = " OR ".join(ph_parts)
    class_df = _fetch_df(
        f"SELECT S_INFO_WINDCODE, CITICS_IND_CODE FROM ASHAREINDUSTRIESCLASSCITICS "
        f"WHERE CUR_SIGN=1 AND ({where})",
        tuple(params),
    )
    if class_df.empty:
        quality["citics_unmapped"] = len(codes)
        return pd.DataFrame(), pos_df[["code", "name", "market_value", "weight"]].copy(), quality

    name_map = load_industry_name_map()
    idx_map = load_citics_l2_index_map()
    class_df = class_df.drop_duplicates("S_INFO_WINDCODE", keep="first")
    class_df["l2_code"] = class_df["CITICS_IND_CODE"].map(citics_to_level2)
    class_df["indus_name"] = class_df["l2_code"].map(name_map)
    class_df["index_code"] = class_df["l2_code"].map(idx_map)
    stock_map = class_df.rename(columns={"S_INFO_WINDCODE": "code", "l2_code": "indus_code"})[
        ["code", "indus_code", "indus_name", "index_code"]
    ]

    index_pct = {}
    if trade_dt and stock_map["index_code"].notna().any():
        idx_codes = stock_map["index_code"].dropna().unique().tolist()
        ph = ",".join(["%s"] * len(idx_codes))
        idx_df = _fetch_df(
            f"SELECT S_INFO_WINDCODE, S_DQ_PCTCHANGE FROM AINDEXINDUSTRIESEODCITICS "
            f"WHERE TRADE_DT=%s AND S_INFO_WINDCODE IN ({ph})",
            (trade_dt, *idx_codes),
        )
        index_pct = dict(zip(idx_df["S_INFO_WINDCODE"], idx_df["S_DQ_PCTCHANGE"].astype(float)))

    industry_df, unmapped = _build_industry_style_df(pos_df, stock_map, index_pct)
    quality["citics_unmapped"] = int(unmapped["code"].nunique()) if not unmapped.empty else 0
    quality["citics_unmapped_mv_pct"] = float(unmapped["weight"].sum()) if not unmapped.empty else 0.0
    quality["citics_industry_count"] = len(industry_df)
    return industry_df, unmapped, quality


def analyze_theme(pos_df: pd.DataFrame, trade_date: str) -> tuple[pd.DataFrame, dict]:
    codes = pos_df["code"].dropna().unique().tolist()
    ph_parts, params = [], []
    for i in range(0, len(codes), BATCH_SIZE):
        batch = codes[i : i + BATCH_SIZE]
        ph = ",".join(["%s"] * len(batch))
        ph_parts.append(f"S_INFO_WINDCODE IN ({ph})")
        params.extend(batch)
    theme_df = _fetch_df(
        f"SELECT S_INFO_WINDCODE, IND_CODE FROM ASHAREWTHEMEINDUSTRIESCLASS "
        f"WHERE CUR_SIGN=1 AND ({' OR '.join(ph_parts)})",
        tuple(params),
    )
    quality = {"theme_stock_theme_pairs": len(theme_df)}
    if theme_df.empty:
        return pd.DataFrame(), quality

    name_map = load_industry_name_map()
    theme_df["theme_code"] = theme_df["IND_CODE"]
    theme_df["theme_name"] = theme_df["IND_CODE"].map(
        lambda c: name_map.get(theme_code_to_industries(str(c)), str(c))
    )

    merged = pos_df.merge(
        theme_df.rename(columns={"S_INFO_WINDCODE": "code"}),
        on="code",
        how="inner",
    )
    # 一只股票可属多主题，权重按主题重复计入（暴露口径）
    records = []
    for (tcode, tname), g in merged.groupby(["theme_code", "theme_name"]):
        tw = g["weight"].sum()
        w_chg = (g["weight"] * g["change_pct"]).sum()
        records.append(
            {
                "theme_code": tcode,
                "theme_name": tname,
                "stock_count": g["code"].nunique(),
                "weight": tw,
                "w_chg_pct": w_chg / tw if tw else 0.0,
                "daily_pnl": sum_daily_pnl(g),
                "profit": g["profit"].sum(),
            }
        )
    out = pd.DataFrame(records)
    if not out.empty:
        out = out.sort_values("weight", ascending=False)
        quality["theme_hhi"] = float((out["weight"] ** 2).sum())
        quality["theme_top1"] = out.iloc[0]["theme_name"]
        quality["theme_top1_weight"] = float(out.iloc[0]["weight"])
    return out, quality
