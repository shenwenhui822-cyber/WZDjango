import pandas as pd

from .codes import wind_to_code_rq
from .config import STRATEGY_TAG
from .mongo import position_col


def get_available_dates(limit: int = 30, *, strategy_tag: str | None = None) -> list[str]:
    tag = (strategy_tag or STRATEGY_TAG).strip()
    cursor = position_col(tag).find({}, {"snapshot_date": 1}).sort("snapshot_date", -1).limit(limit)
    return [d["snapshot_date"] for d in cursor if d.get("snapshot_date")]


def get_latest_snapshot_date(*, strategy_tag: str | None = None) -> str | None:
    dates = get_available_dates(limit=1, strategy_tag=strategy_tag)
    return dates[0] if dates else None


def snapshot_exists(trade_date: str, *, strategy_tag: str | None = None) -> bool:
    tag = (strategy_tag or STRATEGY_TAG).strip()
    return position_col(tag).find_one({"snapshot_date": trade_date}, {"_id": 1}) is not None


def get_prev_snapshot_date(trade_date: str, *, strategy_tag: str | None = None) -> str | None:
    """上一可用持仓快照日期（严格早于 trade_date）。"""
    tag = (strategy_tag or STRATEGY_TAG).strip()
    doc = position_col(tag).find_one(
        {"snapshot_date": {"$lt": trade_date}},
        {"snapshot_date": 1},
        sort=[("snapshot_date", -1)],
    )
    return doc["snapshot_date"] if doc else None


def load_positions_df(trade_date: str, *, strategy_tag: str | None = None) -> pd.DataFrame | None:
    """仅返回持仓 DataFrame；无快照时返回 None。"""
    tag = (strategy_tag or STRATEGY_TAG).strip()
    doc = position_col(tag).find_one({"snapshot_date": trade_date})
    if not doc:
        return None
    account = doc["accounts"][0]
    pos_df = pd.DataFrame(account["positions"])
    if pos_df.empty:
        return None
    pos_df["code_rq"] = pos_df["code"].map(wind_to_code_rq)
    total_mv = pos_df["market_value"].sum()
    pos_df["weight"] = pos_df["market_value"] / total_mv if total_mv else 0.0
    return pos_df


def load_account_meta(trade_date: str, *, strategy_tag: str | None = None) -> dict | None:
    """账户级快照字段；无快照时返回 None。"""
    tag = (strategy_tag or STRATEGY_TAG).strip()
    doc = position_col(tag).find_one({"snapshot_date": trade_date})
    if not doc:
        return None
    account = doc["accounts"][0]
    return {
        "total_asset": account.get("total_asset"),
        "market_value": account.get("market_value"),
        "available_cash": account.get("available_cash"),
        "position_profit": account.get("position_profit"),
    }


def load_position(trade_date: str, *, strategy_tag: str | None = None) -> tuple[pd.DataFrame, dict, dict]:
    """返回 (pos_df, summary, meta)"""
    tag = (strategy_tag or STRATEGY_TAG).strip()
    doc = position_col(tag).find_one({"snapshot_date": trade_date})
    if not doc:
        raise ValueError(f"无持仓快照: {trade_date}")

    pos_df = load_positions_df(trade_date, strategy_tag=tag)
    if pos_df is None:
        raise ValueError(f"持仓为空: {trade_date}")

    account = doc["accounts"][0]
    summary = account.get("position_summary") or {}
    meta = {
        "strategy_tag": doc.get("binding", {}).get("strategy_tag", ""),
        "total_asset": account.get("total_asset"),
        "market_value": account.get("market_value"),
    }
    return pos_df, summary, meta
