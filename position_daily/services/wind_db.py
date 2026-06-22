"""Wind MySQL 连接与通用查询（见 分析计算说明.md）"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

import pandas as pd
import pymysql

from .config import (
    WIND_MYSQL_DATABASE,
    WIND_MYSQL_HOST,
    WIND_MYSQL_PASSWORD,
    WIND_MYSQL_PORT,
    WIND_MYSQL_USER,
)

logger = logging.getLogger("position_daily.wind")

BATCH_SIZE = 300
STRING_COLS = {
    "S_INFO_WINDCODE",
    "INDUSTRIESCODE",
    "INDUSTRIESNAME",
    "S_INFO_NAME",
    "CITICS_IND_CODE",
    "IND_CODE",
    "ROLLING_TYPE",
    "TRADE_DT",
    "EST_DT",
}


def to_trade_dt(trade_date: str) -> str:
    return trade_date.replace("-", "")


def from_trade_dt(trade_dt: str) -> str:
    if len(trade_dt) == 8:
        return f"{trade_dt[:4]}-{trade_dt[4:6]}-{trade_dt[6:8]}"
    return trade_dt


def _new_connection():
    return pymysql.connect(
        host=WIND_MYSQL_HOST,
        port=WIND_MYSQL_PORT,
        user=WIND_MYSQL_USER,
        password=WIND_MYSQL_PASSWORD,
        database=WIND_MYSQL_DATABASE,
        charset="utf8mb4",
        connect_timeout=15,
        read_timeout=300,
        cursorclass=pymysql.cursors.DictCursor,
    )


def wind_conn():
    """每次获取新连接，避免长查询后连接失效影响后续模块。"""
    return _new_connection()


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    for c in df.columns:
        if c in STRING_COLS or c.endswith("_DT"):
            continue
        converted = pd.to_numeric(df[c], errors="coerce")
        if converted.notna().any():
            df[c] = converted
    return df


def _fetch_df(sql: str, params=None) -> pd.DataFrame:
    conn = _new_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall()
        return _coerce_numeric(pd.DataFrame(rows))
    except Exception:
        logger.exception("Wind SQL 失败: %s", sql[:200])
        raise
    finally:
        conn.close()


def resolve_trade_dt(table: str, trade_date: str, date_col: str = "TRADE_DT") -> str | None:
    """取 <= 快照日 的最近可用 Wind 交易日。"""
    dt = to_trade_dt(trade_date)
    sql = (
        f"SELECT MAX({date_col}) AS d FROM {table} "
        f"WHERE {date_col} <= %s"
    )
    df = _fetch_df(sql, (dt,))
    if df.empty or pd.isna(df.iloc[0]["d"]):
        return None
    return str(df.iloc[0]["d"])


def query_in_batches(
    table: str,
    columns: list[str],
    codes: list[str],
    trade_dt: str,
    *,
    code_col: str = "S_INFO_WINDCODE",
    date_col: str = "TRADE_DT",
    extra_where: str = "",
    extra_params: tuple = (),
) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame()
    cols = ", ".join(columns)
    frames = []
    for i in range(0, len(codes), BATCH_SIZE):
        batch = codes[i : i + BATCH_SIZE]
        ph = ",".join(["%s"] * len(batch))
        sql = (
            f"SELECT {cols} FROM {table} "
            f"WHERE {date_col}=%s AND {code_col} IN ({ph})"
        )
        if extra_where:
            sql += f" AND {extra_where}"
        params = (trade_dt, *batch, *extra_params)
        frames.append(_fetch_df(sql, params))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def citics_to_level2(code: str) -> str:
    """中信四级/三级代码 → 二级（16 位 b 开头）。"""
    if not code:
        return code
    c = code.strip()
    if len(c) >= 5:
        return c[:5] + "0" * 11
    return c + "0" * (16 - len(c))


def theme_code_to_industries(code: str) -> str:
    """主题 IND_CODE（10 位）→ ASHAREINDUSTRIESCODE 16 位。"""
    if not code:
        return code
    c = code.strip()
    if len(c) >= 16:
        return c
    return c + "0" * (16 - len(c))


@lru_cache(maxsize=1)
def load_industry_name_map() -> dict[str, str]:
    df = _fetch_df(
        "SELECT INDUSTRIESCODE, INDUSTRIESNAME FROM ASHAREINDUSTRIESCODE"
    )
    if df.empty:
        return {}
    return dict(zip(df["INDUSTRIESCODE"].astype(str), df["INDUSTRIESNAME"].astype(str)))


@lru_cache(maxsize=1)
def load_citics_l2_index_map() -> dict[str, str]:
    """二级行业代码 → 中信行业指数 Wind 代码（CI005xxx.WI）。"""
    ind_df = _fetch_df(
        "SELECT INDUSTRIESCODE, INDUSTRIESNAME FROM ASHAREINDUSTRIESCODE "
        "WHERE LEVELNUM=2 AND INDUSTRIESCODE LIKE 'b%%'"
    )
    idx_df = _fetch_df(
        "SELECT S_INFO_WINDCODE, S_INFO_NAME FROM AINDEXDESCRIPTION "
        "WHERE S_INFO_WINDCODE LIKE 'CI005%%.WI'"
    )
    if ind_df.empty or idx_df.empty:
        return {}

    def _norm(name: str) -> str:
        n = str(name or "")
        for suffix in ("(中信)", "（中信）"):
            n = n.replace(suffix, "")
        return n.strip()

    idx_by_name = {_norm(r["S_INFO_NAME"]): r["S_INFO_WINDCODE"] for _, r in idx_df.iterrows()}
    mapping = {}
    for _, r in ind_df.iterrows():
        code = str(r["INDUSTRIESCODE"])
        name = str(r["INDUSTRIESNAME"])
        idx_code = idx_by_name.get(name)
        if idx_code:
            mapping[code] = idx_code
    logger.info("中信二级行业→指数映射 %d 条", len(mapping))
    return mapping
