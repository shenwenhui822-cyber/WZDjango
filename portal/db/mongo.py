"""MongoDB 连接与 BSON 兼容转换。"""
from __future__ import annotations

from typing import Any

from django.conf import settings
from pymongo import MongoClient
from pymongo.collection import Collection


def get_mongo_client() -> MongoClient:
    return MongoClient(settings.MONGODB_URI)


def get_app_collection() -> Collection:
    client = get_mongo_client()
    db = client[settings.MONGODB_DB_NAME]
    return db[settings.MONGODB_COLLECTION_NAME]


def _allowed_fund_nav_product_keys() -> frozenset[str]:
    return frozenset(
        {
            settings.NAV_REAL_WZ_BSYH_MASTER,
            settings.NAV_REAL_WZ_BSYH_B,
            getattr(settings, "NAV_REAL_WZ_EEH_MASTER", "WZ_EEH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_DYYH_MASTER", "WZ_DYYH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_YLH_MASTER", "WZ_YLH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_SLH_MASTER", "WZ_SLH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_DYLX_MASTER", "WZ_DYLX_MASTER"),
            getattr(settings, "NAV_REAL_WZ_YSH_MASTER", "WZ_YSH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_LLH_MASTER", "WZ_LLH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_LYH_MASTER", "WZ_LYH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_LHJXYH_MASTER", "WZ_LHJXYH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_JLH_MASTER", "WZ_JLH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_CTAYH_MASTER", "WZ_CTAYH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_DYCTAYH_MASTER", "WZ_DYCTAYH_MASTER"),
        }
    )


def get_fund_nav_collection(product_key: str) -> Collection:
    """真实净值：库 MONGODB_FUND_NAV_REAL_DB，集合名 = NAV_REAL_* 产品编码。"""
    key = (product_key or "").strip()
    if key not in _allowed_fund_nav_product_keys():
        raise ValueError(f"未知净值产品编码: {product_key!r}")
    client = get_mongo_client()
    db = client[settings.MONGODB_FUND_NAV_REAL_DB]
    return db[key]


def get_trade_date_collection() -> Collection:
    """交易日历集合：{MONGODB_DB_NAME}.trade_calendar。"""
    client = get_mongo_client()
    db = client[settings.MONGODB_DB_NAME]
    return db[settings.MONGODB_TRADE_CALENDAR_COLLECTION]


def get_t0_performance_collection() -> Collection:
    """T0 日内交易汇总：{MONGODB_T0_PERFORMANCE_DB}.daily_report。"""
    client = get_mongo_client()
    db = client[getattr(settings, "MONGODB_T0_PERFORMANCE_DB", "T0_performance")]
    return db[getattr(settings, "MONGODB_T0_PERFORMANCE_COLLECTION", "daily_report")]


def get_t0_order_collection() -> Collection:
    """T0 周度绩效订单/汇总：{MONGODB_T0_PERFORMANCE_DB}.t0_order。"""
    client = get_mongo_client()
    db = client[getattr(settings, "MONGODB_T0_PERFORMANCE_DB", "T0_performance")]
    return db[getattr(settings, "MONGODB_T0_ORDER_COLLECTION", "t0_order")]


def get_rq_bench_collection() -> Collection:
    """原始指数行情：{MONGODB_RQ_BENCH_DB}.{MONGODB_RQ_BENCH_COLLECTION}。"""
    client = get_mongo_client()
    db = client[settings.MONGODB_RQ_BENCH_DB]
    return db[settings.MONGODB_RQ_BENCH_COLLECTION]


def get_wz_bsyh_htqh_capital_collection() -> Collection:
    """华泰 HT1 普通账单「资金情况」快照：fstock_settle_real.HTZQ_666810103835。"""
    db_name = getattr(
        settings,
        "HTZQ_666810103835_SETTLE_DB",
        "fstock_settle_real",
    )
    coll_name = getattr(
        settings,
        "HTZQ_666810103835_SETTLE_COLLECTION",
        "HTZQ_666810103835",
    )
    client = get_mongo_client()
    db = client[db_name]
    return db[coll_name]


def get_fund_nav_zxdw_nav_collection(collection_name: str) -> Collection:
    """
    五列净值表明细：库 MONGODB_FUND_NAV_REAL_DB（fund_nav_real），集合名为 MONGODB_ZXDW_NAV_COLLECTIONS 之一。
    与博士一号 WZ_BSYH_MASTER / WZ_BSYH_B 同库不同集合。
    """
    allowed = frozenset(getattr(settings, "MONGODB_ZXDW_NAV_COLLECTIONS", ()))
    name = (collection_name or "").strip()
    if name not in allowed:
        raise ValueError(
            f"未知 ZXDW 净值集合名: {collection_name!r}，允许: {sorted(allowed)}"
        )
    client = get_mongo_client()
    db = client[settings.MONGODB_FUND_NAV_REAL_DB]
    return db[name]


def get_rq_bench_calc_collection(calc_collection_name: str) -> Collection:
    """
    计算结果集合：须位于 MONGODB_RQ_BENCH_DB，且集合名以 MONGODB_RQ_BENCH_CALC_PREFIX 开头，
    与原始 rq_bench 区分。
    """
    name = (calc_collection_name or "").strip()
    prefix = getattr(settings, "MONGODB_RQ_BENCH_CALC_PREFIX", "calc_")
    if not name.startswith(prefix):
        raise ValueError(
            f"计算集合名必须以 {prefix!r} 开头，当前: {calc_collection_name!r}"
        )
    client = get_mongo_client()
    db = client[settings.MONGODB_RQ_BENCH_DB]
    return db[name]


def bson_safe_value(v: Any) -> Any:
    """将 pandas/numpy 等类型转为可写入 MongoDB 的基本类型。"""
    if v is None:
        return None
    try:
        import numpy as np
        import pandas as pd

        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            if np.isnan(v) or np.isinf(v):
                return None
            return float(v)
        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            return None
        if pd.isna(v):
            return None
    except Exception:
        pass
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    return v

