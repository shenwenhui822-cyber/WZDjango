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

