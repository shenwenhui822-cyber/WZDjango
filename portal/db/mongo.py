"""MongoDB 连接与 BSON 兼容转换。"""
from __future__ import annotations

import atexit
import re
import threading
from typing import Any

from django.conf import settings

_ALPHA_TARGET_TABLE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
_TRADELOG_TABLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
from pymongo import MongoClient
from pymongo.collection import Collection

_client: MongoClient | None = None
_client_lock = threading.Lock()


def _mongo_client_closed(client: MongoClient) -> bool:
    """判断单例是否已被 close()（管理命令或 atexit 可能关闭）。"""
    try:
        topology = client._topology  # noqa: SLF001 — pymongo 内部状态
        return bool(getattr(topology, "_closed", False))
    except Exception:
        return True


def get_mongo_client() -> MongoClient:
    """进程内复用单个 MongoClient（含连接池），避免每次请求新建连接导致端口耗尽。"""
    global _client
    if _client is not None and not _mongo_client_closed(_client):
        return _client
    with _client_lock:
        if _client is not None and not _mongo_client_closed(_client):
            return _client
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
            _client = None
        _client = MongoClient(settings.MONGODB_URI)
        return _client


def close_mongo_client() -> None:
    """关闭并释放全局 MongoClient（仅进程退出时由 atexit 调用；运行中 Web/调度勿关）。"""
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
            _client = None


atexit.register(close_mongo_client)


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
            getattr(settings, "NAV_REAL_WZ_LLH_A", "WZ_LLH_A"),
            getattr(settings, "NAV_REAL_WZ_LYH_MASTER", "WZ_LYH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_LHJXYH_MASTER", "WZ_LHJXYH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_JLH_MASTER", "WZ_JLH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_CTAYH_MASTER", "WZ_CTAYH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_DYCTAYH_MASTER", "WZ_DYCTAYH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_DWYH_MASTER", "WZ_DWYH_MASTER"),
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


def get_lhjx_position_collection() -> Collection:
    """量化精选一号持仓明细：库 MONGODB_POSITION_FUND_REAL_DB，集合 LHJX（可配）。"""
    client = get_mongo_client()
    db_name = getattr(settings, "MONGODB_POSITION_FUND_REAL_DB", "position_fund_real")
    coll_name = getattr(settings, "MONGODB_LHJX_POSITION_COLLECTION", "LHJX")
    return client[db_name][coll_name]


def get_wzsl_position_collection() -> Collection:
    """吾执三零号持仓明细：库 MONGODB_POSITION_FUND_REAL_DB，集合 WZSL（可配）。"""
    client = get_mongo_client()
    db_name = getattr(settings, "MONGODB_POSITION_FUND_REAL_DB", "position_fund_real")
    coll_name = getattr(settings, "MONGODB_WZSL_POSITION_COLLECTION", "WZSL")
    return client[db_name][coll_name]


def get_alpha_target_position_collection(table_name: str) -> Collection:
    """Alpha 目标持仓：库 MONGODB_ALPHA_TARGET_POSITION_DB，集合名 = csv 文件名（去 .csv）。"""
    name = (table_name or "").strip()
    if not _ALPHA_TARGET_TABLE_RE.fullmatch(name):
        raise ValueError(
            f"非法 Alpha 目标持仓集合名: {table_name!r}（须为字母开头、仅含字母数字下划线）"
        )
    client = get_mongo_client()
    db_name = getattr(
        settings, "MONGODB_ALPHA_TARGET_POSITION_DB", "position_alpha_target"
    )
    return client[db_name][name]


def get_alpha_source_position_collection(table_name: str) -> Collection:
    """Alpha 源持仓：库 MONGODB_ALPHA_SOURCE_POSITION_DB，集合名 = 产品表编码（如 FY1000ZZ）。"""
    name = (table_name or "").strip()
    if not _ALPHA_TARGET_TABLE_RE.fullmatch(name):
        raise ValueError(
            f"非法 Alpha 源持仓集合名: {table_name!r}（须为字母开头、仅含字母数字下划线）"
        )
    client = get_mongo_client()
    db_name = getattr(
        settings, "MONGODB_ALPHA_SOURCE_POSITION_DB", "position_alpha_source"
    )
    return client[db_name][name]


def get_tradelog_db():
    """QMT tradelog 库（集合名 = strategy_tag，如 ZSZQ_911600210）。"""
    client = get_mongo_client()
    db_name = getattr(settings, "MONGODB_TRADELOG_DB", "tradelog")
    return client[db_name]


def list_tradelog_collection_names() -> list[str]:
    db = get_tradelog_db()
    return sorted(
        n for n in db.list_collection_names() if not n.startswith("system.")
    )


def get_tradelog_collection(strategy_tag: str) -> Collection:
    name = (strategy_tag or "").strip()
    if not _TRADELOG_TABLE_RE.fullmatch(name):
        raise ValueError(
            f"非法 tradelog 集合名: {strategy_tag!r}（须为字母开头、仅含字母数字下划线）"
        )
    return get_tradelog_db()[name]


def get_position_close_record_db():
    """收盘账户快照库（集合名与 tradelog 一致）。"""
    client = get_mongo_client()
    db_name = getattr(
        settings, "MONGODB_POSITION_CLOSE_RECORD_DB", "position_close_record"
    )
    return client[db_name]


def list_position_close_record_collection_names() -> list[str]:
    db = get_position_close_record_db()
    return sorted(
        n for n in db.list_collection_names() if not n.startswith("system.")
    )


def get_position_close_record_collection(strategy_tag: str) -> Collection:
    name = (strategy_tag or "").strip()
    if not _TRADELOG_TABLE_RE.fullmatch(name):
        raise ValueError(
            f"非法 position_close_record 集合名: {strategy_tag!r}"
        )
    return get_position_close_record_db()[name]


def get_option_volatility_collection() -> Collection:
    """股指期货 ETF 指标：{MONGODB_OPTION_DB}.volatility（date + ETF_510050 等 9 列）。"""
    client = get_mongo_client()
    db_name = getattr(settings, "MONGODB_OPTION_DB", "option")
    coll_name = getattr(
        settings, "MONGODB_OPTION_VOLATILITY_COLLECTION", "volatility"
    )
    return client[db_name][coll_name]


def get_mail_logs_collection() -> Collection:
    """定时任务运行日志：{MONGODB_MAIL_LOGS_DB}.{MONGODB_MAIL_LOGS_COLLECTION}（默认 mail_logs.MAIL_LOGS）。
    文档含 log_type（success|failure|skipped）、import_succeeded、failure_reason、scheduler_job_key、
    command_name、started_at、finished_at、stdout、stderr、kwargs、target_subject、target_date；
    log_type 为 failure 且存在异常时另有 error_type、error_message、traceback。
    notify_snapshot：管理命令在 stdout 末行输出的 `__MAIL_LOG_RESULT_JSON__:` JSON（与结果邮件字段一致），
    含 data_import_succeeded 等，由调度器解析后与 outcome 合并写入。
    """
    client = get_mongo_client()
    db_name = getattr(settings, "MONGODB_MAIL_LOGS_DB", "mail_logs")
    coll_name = getattr(settings, "MONGODB_MAIL_LOGS_COLLECTION", "MAIL_LOGS")
    return client[db_name][coll_name]


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

