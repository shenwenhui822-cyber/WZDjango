"""MongoDB 连接（Alpha 日报：alpha_product.alpha_sim_nav）。"""
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


def get_trade_date_collection() -> Collection:
    """交易日历集合：alpha_product.trade_calendar。"""
    client = get_mongo_client()
    db = client[settings.MONGODB_TRADE_CALENDAR_DB]
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
