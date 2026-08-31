import ast
import logging

import pandas as pd

from .mongo import rq_db
from .stock_contribution import sum_daily_pnl

logger = logging.getLogger("position_daily.industry")


def get_prev_trade_date(trade_date: str) -> str | None:
    doc = rq_db()["rq_daily_indusSWL2_price"].find_one(
        {"date": {"$lt": trade_date}},
        {"date": 1},
        sort=[("date", -1)],
    )
    return doc["date"] if doc else None


def _build_industry_maps(
    trade_date: str, prev_date: str | None, needed_codes: set[str] | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = list(
        rq_db()["rq_daily_indusSWL2_price"].find(
            {"date": trade_date},
            {"_id": 0, "indus_code": 1, "name": 1, "close": 1, "stocks": 1},
        )
    )
    logger.info("rq_daily_indusSWL2_price 行业数=%d date=%s", len(rows), trade_date)
    prev_close = {}
    if prev_date:
        prev_rows = list(
            rq_db()["rq_daily_indusSWL2_price"].find(
                {"date": prev_date},
                {"_id": 0, "indus_code": 1, "close": 1},
            )
        )
        prev_close = {r["indus_code"]: r["close"] for r in prev_rows if r.get("close")}

    stock_records = []
    price_records = []
    matched_indus = set()
    for r in rows:
        try:
            codes = ast.literal_eval(r["stocks"])
        except (ValueError, SyntaxError):
            codes = []
        hit = False
        for cr in codes:
            if needed_codes is not None and cr not in needed_codes:
                continue
            stock_records.append(
                {"code_rq": cr, "indus_code": r["indus_code"], "indus_name": r["name"]}
            )
            hit = True
        if hit:
            matched_indus.add(r["indus_code"])
        if needed_codes is None or hit:
            pc = prev_close.get(r["indus_code"])
            indus_pct = (r["close"] / pc - 1) * 100 if pc and r.get("close") else None
            price_records.append(
                {
                    "indus_code": r["indus_code"],
                    "indus_name": r["name"],
                    "indus_pct_chg": indus_pct,
                }
            )
    logger.info("行业映射 持仓匹配行业=%d 股票映射=%d", len(matched_indus), len(stock_records))

    map_df = pd.DataFrame(stock_records)
    if not map_df.empty:
        map_df = map_df.drop_duplicates("code_rq", keep="first")
    price_df = pd.DataFrame(price_records)
    return map_df, price_df


def analyze_industry(
    pos_df: pd.DataFrame, trade_date: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """返回 (industry_df, top_good, top_bad, unmapped_df, quality)"""
    prev_date = get_prev_trade_date(trade_date)
    needed = set(pos_df["code_rq"].dropna())
    map_df, price_df = _build_industry_maps(trade_date, prev_date, needed_codes=needed)

    if map_df.empty:
        empty = pd.DataFrame()
        quality = {
            "indus_unmapped": len(pos_df),
            "indus_unmapped_mv_pct": 1.0,
            "prev_trade_date": prev_date,
            "rq_indus_date": trade_date,
        }
        return empty, empty, empty, pos_df[["code", "name", "market_value", "weight"]].copy(), quality

    merged = pos_df.merge(map_df, on="code_rq", how="left")
    merged = merged.merge(price_df, on="indus_code", how="left", suffixes=("", "_p"))

    unmapped_df = merged[merged["indus_code"].isna()][
        ["code", "name", "market_value", "weight"]
    ].copy()

    mapped = merged.dropna(subset=["indus_code"]).copy()

    records = []
    for (icode, iname), g in mapped.groupby(["indus_code", "indus_name"]):
        indus_weight = g["weight"].sum()
        w_chg = (g["weight"] * g["change_pct"]).sum()
        # 行业内市值加权涨跌（%），与行业指数涨跌同一量纲
        w_chg_pct = w_chg / indus_weight if indus_weight else 0.0
        indus_pct = g["indus_pct_chg"].iloc[0]
        records.append(
            {
                "indus_code": icode,
                "indus_name": iname,
                "stock_count": len(g),
                "weight": indus_weight,
                "w_chg_pct": w_chg_pct,
                "indus_pct_chg": indus_pct,
                "excess_pct": (w_chg_pct - indus_pct) if pd.notna(indus_pct) else float("nan"),
                "daily_pnl": sum_daily_pnl(g),
                "profit": g["profit"].sum(),
            }
        )
    industry_df = pd.DataFrame(records)
    if not industry_df.empty:
        industry_df = industry_df.sort_values("weight", ascending=False)
        industry_df["excess_pct"] = pd.to_numeric(industry_df["excess_pct"], errors="coerce")

    ranked = industry_df.dropna(subset=["excess_pct"]) if not industry_df.empty else industry_df
    top_good = ranked.nlargest(5, "excess_pct") if not ranked.empty else industry_df.head(0)
    top_bad = ranked.nsmallest(5, "excess_pct") if not ranked.empty else industry_df.head(0)

    quality = {
        "indus_unmapped": int(len(unmapped_df)),
        "indus_unmapped_mv_pct": float(unmapped_df["weight"].sum()) if len(unmapped_df) else 0.0,
        "prev_trade_date": prev_date,
        "rq_indus_date": trade_date,
        "industry_covered": int(mapped["indus_code"].nunique()) if not mapped.empty else 0,
    }
    return industry_df, top_good, top_bad, unmapped_df, quality
