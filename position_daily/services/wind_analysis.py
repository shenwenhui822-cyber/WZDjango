"""通联（tldb）维度分析：估值、资金流、盈利预期、中信行业、主题"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .stock_contribution import sum_daily_pnl
from .wind_db import (
    BATCH_SIZE,
    _fetch_df,
    attach_wind_code,
    chg_pct_to_percent,
    citics_to_level2,
    from_trade_dt,
    load_citics_l2_index_map,
    load_citics_l2_name_map,
    load_theme_l2_name_map,
    query_by_tickers,
    resolve_trade_dt,
    wind_code_to_ticker,
)

logger = logging.getLogger("position_daily.wind")

# 同一次报告内复用 mkt_equd_eval，避免估值/预期/市值分桶重复打库
_EVAL_CACHE: dict[tuple[str, tuple[str, ...]], pd.DataFrame] = {}


def _weighted_avg(values: pd.Series, weights: pd.Series) -> float | None:
    mask = values.notna() & weights.notna() & (weights > 0)
    if not mask.any():
        return None
    w = weights[mask]
    v = values[mask].astype(float)
    return float((w * v).sum() / w.sum())


def _pos_tickers(pos_df: pd.DataFrame) -> list[str]:
    return sorted({wind_code_to_ticker(c) for c in pos_df["code"].dropna().unique().tolist() if c})


def _ticker_to_code_map(pos_df: pd.DataFrame) -> dict[str, str]:
    """TICKER → 持仓 Wind 代码（同代码多交易所时以后出现的为准，极少见）。"""
    mapping: dict[str, str] = {}
    for code in pos_df["code"].dropna().unique().tolist():
        mapping[wind_code_to_ticker(code)] = str(code)
    return mapping


def _fetch_eval(pos_df: pd.DataFrame, trade_dt: str) -> pd.DataFrame:
    """mkt_equd_eval 按 TICKER 批量取数（无 JOIN），映射回持仓 code；进程内缓存。"""
    tickers = tuple(_pos_tickers(pos_df))
    if not tickers or not trade_dt:
        return pd.DataFrame()
    cache_key = (trade_dt, tickers)
    cached = _EVAL_CACHE.get(cache_key)
    if cached is not None:
        return cached.copy()

    frames = []
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = list(tickers[i : i + BATCH_SIZE])
        ph = ",".join(["%s"] * len(batch))
        sql = (
            "SELECT TICKER_SYMBOL, PE_T, PB, MARKET_VALUE, PE_CM "
            f"FROM mkt_equd_eval WHERE TRADE_DATE=%s AND TICKER_SYMBOL IN ({ph})"
        )
        frames.append(_fetch_df(sql, (trade_dt, *batch)))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        _EVAL_CACHE[cache_key] = df
        return df

    code_map = _ticker_to_code_map(pos_df)
    df["code"] = df["TICKER_SYMBOL"].astype(str).map(code_map)
    df = df.dropna(subset=["code"]).drop_duplicates("code", keep="first")
    _EVAL_CACHE[cache_key] = df
    return df.copy()


def clear_eval_cache() -> None:
    _EVAL_CACHE.clear()


def analyze_valuation(pos_df: pd.DataFrame, trade_date: str) -> tuple[dict, dict]:
    """组合加权 PE/PB/市值及市值分位（mkt_equd_eval）。"""
    trade_dt = resolve_trade_dt("mkt_equd_eval", trade_date)
    quality = {
        "wind_deriv_trade_dt": trade_dt,
        "wind_deriv_date": from_trade_dt(trade_dt) if trade_dt else None,
    }
    if not trade_dt:
        return {}, quality

    deriv = _fetch_eval(pos_df, trade_dt)
    if deriv.empty:
        quality["deriv_matched"] = 0
        return {}, quality

    merged = pos_df.merge(
        deriv[["code", "PE_T", "PB", "MARKET_VALUE", "PE_CM"]],
        on="code",
        how="left",
    )
    matched = merged[merged["MARKET_VALUE"].notna()].copy()
    quality["deriv_matched"] = int(matched["code"].nunique())
    quality["deriv_coverage"] = float(matched["weight"].sum())

    # 全市场截面扫描极慢；改用持仓内市值分位（秒级）
    matched["mv_pct"] = matched["MARKET_VALUE"].rank(pct=True) * 100
    quality["mv_pct_universe"] = "portfolio"

    summary = {
        "w_pe_ttm": _weighted_avg(matched["PE_T"], matched["weight"]),
        "w_pb": _weighted_avg(matched["PB"], matched["weight"]),
        "w_mv_yi": _weighted_avg(matched["MARKET_VALUE"] / 1e8, matched["weight"]),
        "w_mv_pct": _weighted_avg(matched["mv_pct"], matched["weight"]),
        "median_pe_ttm": float(matched["PE_T"].median()) if matched["PE_T"].notna().any() else None,
        "median_pb": float(matched["PB"].median()) if matched["PB"].notna().any() else None,
    }
    return summary, quality


def analyze_moneyflow(pos_df: pd.DataFrame, trade_date: str) -> tuple[dict, dict]:
    """资金流向：mkt_equ_mf_new（超大单/大单净流入）。"""
    trade_dt = resolve_trade_dt("mkt_equ_mf_new", trade_date)
    quality = {"wind_mf_trade_dt": trade_dt}
    if not trade_dt:
        return {}, quality

    tickers = _pos_tickers(pos_df)
    mf = query_by_tickers(
        "mkt_equ_mf_new",
        ["TICKER_SYMBOL", "NET_FLOW_XL", "NET_FLOW_L"],
        tickers,
        trade_dt,
    )
    if mf.empty:
        quality["mf_matched"] = 0
        return {}, quality

    code_map = _ticker_to_code_map(pos_df)
    mf["code"] = mf["TICKER_SYMBOL"].astype(str).map(code_map)
    mf = mf.dropna(subset=["code"]).drop_duplicates("code", keep="first")
    mf["net_exlarge"] = pd.to_numeric(mf["NET_FLOW_XL"], errors="coerce")
    mf["net_large"] = pd.to_numeric(mf["NET_FLOW_L"], errors="coerce")

    merged = pos_df.merge(mf[["code", "net_exlarge", "net_large"]], on="code", how="left")
    matched = merged[merged["net_exlarge"].notna()]
    quality["mf_matched"] = int(matched["code"].nunique())
    quality["mf_coverage"] = float(matched["weight"].sum())

    total_mv = float(pos_df["market_value"].sum()) or 1.0
    net_ex = float(matched["net_exlarge"].sum())
    net_lg = float(matched["net_large"].sum())

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
    """盈利预期：复用 mkt_equd_eval.PE_CM（与估值同一次取数缓存）。"""
    trade_dt = resolve_trade_dt("mkt_equd_eval", trade_date)
    quality = {"wind_consensus_est_dt": trade_dt.replace("-", "") if trade_dt else None}
    if not trade_dt:
        return {}, quality

    deriv = _fetch_eval(pos_df, trade_dt)
    if deriv.empty or "PE_CM" not in deriv.columns:
        quality["consensus_matched"] = 0
        quality["consensus_coverage"] = 0.0
        return {}, quality

    merged = pos_df.merge(deriv[["code", "PE_CM"]], on="code", how="left")
    matched = merged[merged["PE_CM"].notna()].copy()
    quality["consensus_matched"] = int(matched["code"].nunique())
    quality["consensus_coverage"] = float(matched["weight"].sum()) if not matched.empty else 0.0
    quality["consensus_prev_est_dt"] = None

    summary = {
        "w_est_pe": _weighted_avg(matched["PE_CM"], matched["weight"]) if not matched.empty else None,
        "w_net_profit_yi": None,
        "w_np_chg_pct": None,
        "coverage_count": quality["consensus_matched"],
        "coverage_pct": quality["consensus_coverage"],
    }
    return summary, quality


def _build_industry_style_df(
    pos_df: pd.DataFrame,
    stock_map: pd.DataFrame,
    index_pct: dict[str, float],
    code_name_col: str = "indus_code",
) -> tuple[pd.DataFrame, pd.DataFrame]:
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
                "excess_pct": (w_chg_pct - indus_pct) if pd.notna(indus_pct) else np.nan,
                "daily_pnl": sum_daily_pnl(g),
                "profit": g["profit"].sum(),
            }
        )
    df = pd.DataFrame(records)
    if not df.empty:
        df["excess_pct"] = pd.to_numeric(df["excess_pct"], errors="coerce")
        df["indus_pct_chg"] = pd.to_numeric(df["indus_pct_chg"], errors="coerce")
        df = df.sort_values("weight", ascending=False)
    return df, unmapped


def _fetch_inst_types(
    tickers: list[str],
    industry: str,
    type_prefix: str,
) -> pd.DataFrame:
    """md_security → PARTY_ID，再按 TYPE_ID 前缀取分类（两段查询，避免大 JOIN）。"""
    if not tickers:
        return pd.DataFrame()
    sec_frames = []
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        ph = ",".join(["%s"] * len(batch))
        sec_frames.append(
            _fetch_df(
                "SELECT TICKER_SYMBOL, EXCHANGE_CD, PARTY_ID FROM md_security "
                f"WHERE TICKER_SYMBOL IN ({ph}) AND EXCHANGE_CD IN ('XSHG','XSHE','BJSE')",
                tuple(batch),
            )
        )
    sec = pd.concat(sec_frames, ignore_index=True) if sec_frames else pd.DataFrame()
    if sec.empty:
        return pd.DataFrame()
    sec = sec.dropna(subset=["PARTY_ID"]).drop_duplicates(["TICKER_SYMBOL", "EXCHANGE_CD"], keep="first")
    party_ids = [int(x) for x in sec["PARTY_ID"].unique().tolist()]

    type_frames = []
    for i in range(0, len(party_ids), BATCH_SIZE):
        batch = party_ids[i : i + BATCH_SIZE]
        ph = ",".join(["%s"] * len(batch))
        type_frames.append(
            _fetch_df(
                "SELECT i.PARTY_ID, i.TYPE_ID, t.TYPE_NAME, t.INDUSTRY_LEVEL "
                "FROM md_inst_type i "
                "INNER JOIN md_type t ON i.TYPE_ID = t.TYPE_ID "
                f"WHERE i.IS_NEW=1 AND i.PARTY_ID IN ({ph}) "
                "AND t.INDUSTRY=%s AND i.TYPE_ID LIKE %s",
                (*batch, industry, f"{type_prefix}%"),
            )
        )
    types = pd.concat(type_frames, ignore_index=True) if type_frames else pd.DataFrame()
    if types.empty:
        return pd.DataFrame()
    out = sec.merge(types, on="PARTY_ID", how="inner")
    return attach_wind_code(out)


def analyze_citics_industry(pos_df: pd.DataFrame, trade_date: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """中信二级：md_inst_type(中信行业分类) + mkt_idxd_citic。"""
    trade_dt = resolve_trade_dt("mkt_idxd_citic", trade_date)
    quality = {"wind_citics_trade_dt": trade_dt.replace("-", "") if trade_dt else None}
    tickers = _pos_tickers(pos_df)
    if not tickers:
        return pd.DataFrame(), pos_df[["code", "name", "market_value", "weight"]].copy(), quality

    class_df = _fetch_inst_types(tickers, "中信行业分类", "010317")
    if class_df.empty:
        quality["citics_unmapped"] = len(pos_df)
        return pd.DataFrame(), pos_df[["code", "name", "market_value", "weight"]].copy(), quality

    class_df["INDUSTRY_LEVEL"] = pd.to_numeric(class_df["INDUSTRY_LEVEL"], errors="coerce").fillna(0)
    class_df = class_df.sort_values("INDUSTRY_LEVEL", ascending=False).drop_duplicates("code", keep="first")
    class_df["indus_code"] = class_df["TYPE_ID"].map(citics_to_level2)
    name_map = load_citics_l2_name_map()
    idx_map = load_citics_l2_index_map()
    class_df["indus_name"] = class_df["indus_code"].map(name_map)
    class_df["index_code"] = class_df["indus_code"].map(idx_map)
    stock_map = class_df[["code", "indus_code", "indus_name", "index_code"]]

    index_pct: dict[str, float] = {}
    if trade_dt and stock_map["index_code"].notna().any():
        idx_codes = stock_map["index_code"].dropna().unique().tolist()
        ph = ",".join(["%s"] * len(idx_codes))
        idx_df = _fetch_df(
            f"SELECT TICKER_SYMBOL, CHG_PCT FROM mkt_idxd_citic "
            f"WHERE TRADE_DATE=%s AND TICKER_SYMBOL IN ({ph})",
            (trade_dt, *idx_codes),
        )
        if not idx_df.empty:
            index_pct = dict(
                zip(idx_df["TICKER_SYMBOL"].astype(str), chg_pct_to_percent(idx_df["CHG_PCT"]))
            )

    industry_df, unmapped = _build_industry_style_df(pos_df, stock_map, index_pct)
    quality["citics_unmapped"] = int(unmapped["code"].nunique()) if not unmapped.empty else 0
    quality["citics_unmapped_mv_pct"] = float(unmapped["weight"].sum()) if not unmapped.empty else 0.0
    quality["citics_industry_count"] = len(industry_df)
    return industry_df, unmapped, quality


def analyze_theme(pos_df: pd.DataFrame, trade_date: str) -> tuple[pd.DataFrame, dict]:
    """主题：战略性新兴产业(2018) 二级（md_inst_type）。"""
    del trade_date
    tickers = _pos_tickers(pos_df)
    quality: dict = {"theme_stock_theme_pairs": 0}
    if not tickers:
        return pd.DataFrame(), quality

    theme_df = _fetch_inst_types(tickers, "战略性新兴产业(2018)", "010319")
    quality["theme_stock_theme_pairs"] = len(theme_df)
    if theme_df.empty:
        return pd.DataFrame(), quality

    theme_df["theme_code"] = theme_df["TYPE_ID"].map(citics_to_level2)
    name_map = load_theme_l2_name_map()
    theme_df["theme_name"] = theme_df["theme_code"].map(name_map).fillna(theme_df["theme_code"])
    theme_df = theme_df.drop_duplicates(["code", "theme_code"], keep="first")

    merged = pos_df.merge(theme_df[["code", "theme_code", "theme_name"]], on="code", how="inner")
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
