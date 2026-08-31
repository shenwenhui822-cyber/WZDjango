"""通联 MySQL（tldb）连接与通用查询。表目录见根目录 _tlnew_utf8.csv。"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
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

BATCH_SIZE = 800  # 单次 IN 覆盖整仓，减少往返

_tls = threading.local()

EXCHANGE_TO_SUFFIX = {"XSHG": "SH", "XSHE": "SZ", "BJSE": "BJ"}
SUFFIX_TO_EXCHANGE = {v: k for k, v in EXCHANGE_TO_SUFFIX.items()}

STRING_COLS = {
    "TICKER_SYMBOL",
    "EXCHANGE_CD",
    "TYPE_ID",
    "TYPE_NAME",
    "IND_ID",
    "SEC_SHORT_NAME",
    "THEME_CODE",
    "THEME_NAME",
    "code",
}


def to_trade_dt(trade_date: str) -> str:
    """统一为 YYYY-MM-DD（通联 TRADE_DATE）。"""
    s = str(trade_date).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def from_trade_dt(trade_dt) -> str | None:
    if trade_dt is None or (isinstance(trade_dt, float) and pd.isna(trade_dt)):
        return None
    if isinstance(trade_dt, (date, datetime)):
        return trade_dt.strftime("%Y-%m-%d")
    s = str(trade_dt).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10] if s else None


def wind_code_to_ticker(code: str) -> str:
    if not code or "." not in str(code):
        return str(code or "")
    return str(code).split(".", 1)[0]


def wind_code_to_exchange(code: str) -> str | None:
    if not code or "." not in str(code):
        return None
    suf = str(code).upper().split(".", 1)[1]
    return SUFFIX_TO_EXCHANGE.get(suf)


def to_wind_code(ticker: str, exchange_cd: str) -> str:
    suf = EXCHANGE_TO_SUFFIX.get(str(exchange_cd).upper(), "")
    return f"{ticker}.{suf}" if suf else str(ticker)


def chg_pct_to_percent(series: pd.Series) -> pd.Series:
    """通联 CHG_PCT 为小数（0.01=1%）→ 报告用百分数。"""
    return pd.to_numeric(series, errors="coerce") * 100


def _new_connection():
    t0 = time.perf_counter()
    conn = pymysql.connect(
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
    logger.info("tldb 建连耗时 %.0fms", (time.perf_counter() - t0) * 1000)
    return conn


@contextmanager
def tldb_session():
    """整份报告共用一条 MySQL 连接（建连约 10s，绝不可每条 SQL 新建）。"""
    owned = False
    if getattr(_tls, "conn", None) is None:
        _tls.conn = _new_connection()
        _tls.depth = 0
        owned = True
    _tls.depth = getattr(_tls, "depth", 0) + 1
    try:
        yield _tls.conn
    finally:
        _tls.depth -= 1
        if owned and _tls.depth <= 0:
            try:
                _tls.conn.close()
            except Exception:
                pass
            _tls.conn = None
            _tls.depth = 0


def close_tldb_session() -> None:
    """关闭线程内自动/会话连接。"""
    conn = getattr(_tls, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _tls.conn = None
    _tls.depth = 0


def _ensure_conn():
    """懒建连并挂到线程，后续 SQL 全部复用。"""
    conn = getattr(_tls, "conn", None)
    if conn is None:
        conn = _new_connection()
        _tls.conn = conn
        _tls.depth = 1
    return conn


def wind_conn():
    """返回当前线程复用连接。"""
    return _ensure_conn()


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    for c in df.columns:
        if c in STRING_COLS or c.endswith("_DATE") or c.endswith("_DT") or c in ("TRADE_DATE",):
            continue
        if c in ("SECURITY_ID", "PARTY_ID", "INDEX_ID", "TYPE_ID"):
            continue
        converted = pd.to_numeric(df[c], errors="coerce")
        if converted.notna().any():
            df[c] = converted
    return df


def _fetch_df(sql: str, params=None) -> pd.DataFrame:
    """始终复用线程连接（建连约 10s，绝不可每条 SQL 新建）。"""
    conn = _ensure_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall()
        return _coerce_numeric(pd.DataFrame(rows))
    except Exception:
        logger.exception("tldb SQL 失败: %s", sql[:200])
        raise


def resolve_trade_dt(table: str, trade_date: str, date_col: str = "TRADE_DATE") -> str | None:
    """取 <= 快照日 的最近可用交易日（YYYY-MM-DD）。样本股等值探测，须在 tldb_session 内调用。"""
    return _resolve_trade_dt_cached(table, to_trade_dt(trade_date), date_col)


@lru_cache(maxsize=128)
def _resolve_trade_dt_cached(table: str, dt: str, date_col: str) -> str | None:
    from datetime import datetime, timedelta

    anchor = "600519"
    probe_tables = {
        "mkt_equd_eval",
        "mkt_equ_mf_new",
        "mkt_equd",
        "mkt_idxd",
        "mkt_idxd_citic",
    }
    if table in probe_tables:
        try:
            base = datetime.strptime(dt, "%Y-%m-%d").date()
        except ValueError:
            base = None
        if base is not None:
            # 只探交易日回退：0,1,2,3,4 然后跳周末感的 7,8… 最多约 10 次
            for offset in (0, 1, 2, 3, 4, 7, 8, 9, 10, 11):
                day = (base - timedelta(days=offset)).isoformat()
                try:
                    df = _fetch_df(
                        f"SELECT {date_col} AS d FROM {table} "
                        f"WHERE TICKER_SYMBOL=%s AND {date_col}=%s LIMIT 1",
                        (anchor, day),
                    )
                    if not df.empty and pd.notna(df.iloc[0]["d"]):
                        return from_trade_dt(df.iloc[0]["d"])
                except Exception:
                    logger.exception("resolve_trade_dt 等值探测失败 table=%s day=%s", table, day)

    df = _fetch_df(
        f"SELECT MAX({date_col}) AS d FROM {table} "
        f"WHERE TICKER_SYMBOL=%s AND {date_col} <= %s",
        (anchor, dt),
    )
    if df.empty or pd.isna(df.iloc[0]["d"]):
        return None
    return from_trade_dt(df.iloc[0]["d"])


def query_by_tickers(
    table: str,
    columns: list[str],
    tickers: list[str],
    trade_dt: str,
    *,
    ticker_col: str = "TICKER_SYMBOL",
    date_col: str = "TRADE_DATE",
    extra_where: str = "",
    extra_params: tuple = (),
) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    cols = ", ".join(columns)
    frames = []
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        ph = ",".join(["%s"] * len(batch))
        sql = (
            f"SELECT {cols} FROM {table} "
            f"WHERE {date_col}=%s AND {ticker_col} IN ({ph})"
        )
        if extra_where:
            sql += f" AND {extra_where}"
        params = (trade_dt, *batch, *extra_params)
        frames.append(_fetch_df(sql, params))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


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
    """兼容旧调用：Wind 代码列表 → 按 TICKER_SYMBOL 查通联表。"""
    tickers = [wind_code_to_ticker(c) for c in codes if c]
    date_col_tl = "TRADE_DATE" if date_col in ("TRADE_DT", "EST_DT") else date_col
    cols = []
    for c in columns:
        if c == "S_INFO_WINDCODE":
            cols.append("TICKER_SYMBOL")
        else:
            cols.append(c)
    # 去重保持顺序
    seen = set()
    uniq_cols = []
    for c in cols:
        if c not in seen:
            seen.add(c)
            uniq_cols.append(c)
    df = query_by_tickers(
        table,
        uniq_cols,
        tickers,
        to_trade_dt(trade_dt) if "-" not in str(trade_dt) and len(str(trade_dt)) == 8 else to_trade_dt(trade_dt),
        date_col=date_col_tl,
        extra_where=extra_where,
        extra_params=extra_params,
    )
    return df


def attach_wind_code(df: pd.DataFrame, ticker_col: str = "TICKER_SYMBOL", exch_col: str = "EXCHANGE_CD") -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["code"] = [
        to_wind_code(str(t), str(e)) for t, e in zip(out[ticker_col], out[exch_col])
    ]
    return out


@lru_cache(maxsize=1)
def load_citics_l2_index_map() -> dict[str, str]:
    """中信二级 TYPE_ID → 指数 TICKER_SYMBOL（CI005xxx）。"""
    df = _fetch_df(
        "SELECT IND_ID, TICKER_SYMBOL FROM idx "
        "WHERE INDEX_GROUP='CITIC' AND IND_ID IS NOT NULL "
        "AND CHAR_LENGTH(IND_ID)=10 AND TICKER_SYMBOL LIKE 'CI005%%'"
    )
    if df.empty:
        return {}
    mapping = dict(zip(df["IND_ID"].astype(str), df["TICKER_SYMBOL"].astype(str)))
    logger.info("中信二级行业→指数映射 %d 条", len(mapping))
    return mapping


@lru_cache(maxsize=1)
def load_citics_l2_name_map() -> dict[str, str]:
    df = _fetch_df(
        "SELECT TYPE_ID, TYPE_NAME FROM md_type "
        "WHERE INDUSTRY=%s AND INDUSTRY_LEVEL=2",
        ("中信行业分类",),
    )
    if df.empty:
        return {}
    return dict(zip(df["TYPE_ID"].astype(str), df["TYPE_NAME"].astype(str)))


@lru_cache(maxsize=1)
def load_theme_l2_name_map() -> dict[str, str]:
    """战略性新兴产业二级 TYPE_ID → 名称。"""
    df = _fetch_df(
        "SELECT TYPE_ID, TYPE_NAME FROM md_type "
        "WHERE INDUSTRY=%s AND INDUSTRY_LEVEL=2",
        ("战略性新兴产业(2018)",),
    )
    if df.empty:
        return {}
    return dict(zip(df["TYPE_ID"].astype(str), df["TYPE_NAME"].astype(str)))


def citics_to_level2(type_id: str) -> str:
    """中信 TYPE_ID → 二级（10 位）。"""
    c = str(type_id or "").strip()
    if len(c) >= 10:
        return c[:10]
    return c


def theme_code_to_industries(code: str) -> str:
    """兼容旧接口：战略主题 TYPE_ID → 二级。"""
    return citics_to_level2(code)


def load_industry_name_map() -> dict[str, str]:
    """兼容旧接口：合并中信 + 战略二级名称。"""
    m = {}
    m.update(load_citics_l2_name_map())
    m.update(load_theme_l2_name_map())
    return m
