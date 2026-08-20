"""
独立脚本：从米筐拉取指数行情，写入 basic_rq.rq_bench。

指数说明：万得全 A（业务上沿用 code=881001.WI）在米筐无直接对应合约，
行情使用国证 A 指 399317（order_book_id：399317.XSHE）。落库仍为 code='881001.WI'、
code_rq='399317.XSHE'，与既有 load_data 查询兼容。

依赖：rqdatac 已 init。

传入的交易日即拉取并写入 Mongo，无额外日期门槛。
股指部分固定见 BENCH_CODE_TO_RQ；中金所股指期货 IF/IH/IC/IM 各取米筐当日
`futures.get_contracts` 前 4 个可交易合约（随交割月自动滚动）。

用法（项目根，PYTHONPATH=.）:
  python update_rq_bench_17.py              # 未写日期时默认本年 4 月 14 日
  python update_rq_bench_17.py 2025-12-01
  python update_rq_bench_17.py 2026-04-21 --dry-run   # 只拉行情并打印，不落库、不建索引
  python -c "from update_rq_bench_17 import update_rq_bench; update_rq_bench('2025-12-01')"
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np
import pandas as pd
import pymongo
import rqdatac as rq
from dotenv import load_dotenv

# 项目根目录（WZDjango）
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))

try:
    rq.init("15317321758", "WuZhi@2026")
    print("[OK] RQData 连接成功 (update_rq_bench_17)")
except Exception as e:
    print(f"[ERR] RQData 连接失败: {e}")
    raise


def get_client() -> pymongo.MongoClient:
    env_uri = (os.getenv("MONGODB_URI") or "").strip()
    if env_uri:
        return pymongo.MongoClient(env_uri)
    raise RuntimeError("未配置 MONGODB_URI：请在项目根目录 .env 中设置。")


def _norm_day(s: str) -> str:
    s = str(s).strip()
    if "/" in s:
        return pd.Timestamp(s.replace("/", "-")).strftime("%Y-%m-%d")
    return pd.Timestamp(s).strftime("%Y-%m-%d")


def _day_variants(s: str) -> list[str]:
    s = _norm_day(s)
    y, m, d = s.split("-")
    return list(dict.fromkeys([s, f"{y}/{m}/{d}"]))


def _df_nan_to_none(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace({np.nan: None})


# 落库业务代码 code → 米筐 order_book_id（code_rq）
# rq_bench_substitute：万得全 A 用国证 A 指代用时为 True
BENCH_CODE_TO_RQ: list[tuple[str, str]] = [
    ("000001.SH", "000001.XSHG"),
    ("399001.SZ", "399001.XSHE"),
    # 万得的全A指数（881001.WI）米筐无对应合约，行情用国证A指 399317（order_book_id：399317.XSHE）
    ("881001.WI", "399317.XSHE"),
    ("000300.SH", "000300.XSHG"),
    # 米筐上中证500/1000 指数行情用深交所合约（.XSHG）；落库 code 用 .SH 与之一致
    ("000905.SH", "000905.XSHG"),
    ("000852.SH", "000852.XSHG"),
    # 中证科创创业50（通达信 931643）；米筐：931643.INDX（见 RQ indices-mod 指数表）
    ("931643", "931643.INDX"),
    # 上证红利指数（通达信 000015）；米筐：000015.XSHG（文档「红利指数」）
    ("000015.SH", "000015.XSHG"),
]

# 使用代用米筐合约的行情、但 code 仍为左侧业务码的集合
_SUBSTITUTE_BENCH_CODES = {"881001.WI"}

# 落库数值精度：价格/量额 2 位，涨跌幅 4 位（四舍五入）
BENCH_PRICE_VOL_COLS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amt",
)
PCT_CHG_COL = "pct_chg"


def _round_half_up(value: Any, places: int) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    quant = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def format_bench_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for col in BENCH_PRICE_VOL_COLS:
        if col in out:
            out[col] = _round_half_up(out[col], 2)
    if PCT_CHG_COL in out:
        out[PCT_CHG_COL] = _round_half_up(out[PCT_CHG_COL], 4)
    return out

# 中金所股指期货：各品种当日可交易合约（通常 4 个），由米筐按到期剔除/挂牌
_CFFEX_STOCK_INDEX_UNDERLYINGS: tuple[str, ...] = ("IF", "IH", "IC", "IM")


def cffex_futures_bench_pairs(trade_day: str) -> list[tuple[str, str]]:
    """IF/IH/IC/IM：`(code, code_rq)` 均用合约 order_book_id；合约列表随交易日滚动。"""
    trade_day = _norm_day(trade_day)
    out: list[tuple[str, str]] = []
    for sym in _CFFEX_STOCK_INDEX_UNDERLYINGS:
        ids = rq.futures.get_contracts(sym, trade_day) or []
        for oid in ids[:4]:
            out.append((oid, oid))
    return out


def bench_pairs_for_day(trade_day: str) -> list[tuple[str, str]]:
    """静态股指 + 当日中金所股指期货合约。"""
    return list(BENCH_CODE_TO_RQ) + cffex_futures_bench_pairs(trade_day)


def _rq_close_series(rq_id: str, end_day: str, lookback_days: int = 45) -> pd.Series | None:
    """拉取窗口内日收盘序列（当前 rqdatac 的 get_price 不支持 count=）。"""
    end_day = _norm_day(end_day)
    start = (pd.Timestamp(end_day) - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    prev = rq.get_price(
        rq_id,
        start_date=start,
        end_date=end_day,
        frequency="1d",
        fields=["close"],
        expect_df=True,
    )
    if prev is None or (isinstance(prev, pd.DataFrame) and prev.empty):
        return None
    s = prev["close"].dropna()
    return s if len(s) else None


def _bench_row(bench_code: str, rq_id: str, day: str) -> dict[str, Any]:
    day = _norm_day(day)
    try:
        bar = rq.get_price(
            rq_id,
            start_date=day,
            end_date=day,
            frequency="1d",
            fields=["open", "high", "low", "close", "volume", "total_turnover"],
            expect_df=True,
        )
        if bar is None or (isinstance(bar, pd.DataFrame) and bar.empty):
            return {}
        if isinstance(bar, pd.DataFrame) and "order_book_id" in bar.columns:
            bar = bar[bar["order_book_id"] == rq_id]
        closes = _rq_close_series(rq_id, day)
        if closes is None or len(closes) < 2:
            pct = None
            pre_close = None
        else:
            c0 = float(closes.iloc[-2])
            c1 = float(closes.iloc[-1])
            pct = (c1 / c0 - 1) if c0 else None
            pre_close = c0
        row = bar.iloc[-1]
        vol = float(row["volume"]) if "volume" in row.index and pd.notna(row["volume"]) else 0.0
        amt = float(row["total_turnover"]) if "total_turnover" in row.index and pd.notna(row["total_turnover"]) else None
        close = float(row["close"])
        rq_index_code = str(rq_id).split(".")[0] if rq_id else ""
        rq_bench_substitute = bench_code in _SUBSTITUTE_BENCH_CODES
        raw = {
            "date": day,
            "code": bench_code,
            "code_rq": rq_id,
            "rq_index_code": rq_index_code,
            "rq_bench_substitute": rq_bench_substitute,
            "pct_chg": pct,
            "volume": vol / 1_000_000,
            "amt": amt,
            "pre_close": pre_close,
            "close": close,
            "open": float(row["open"]) if "open" in row.index else None,
            "high": float(row["high"]) if "high" in row.index else None,
            "low": float(row["low"]) if "low" in row.index else None,
        }
        return format_bench_row(raw)
    except Exception as e:
        print(f"[WARN] 基准 {bench_code} / {rq_id} 失败: {e}")
        return {}


def update_rq_bench(
    pre_trade_day: str,
    *,
    mongo_db: str = "basic_rq",
    target_coll: str = "rq_bench",
    dry_run: bool = False,
) -> bool:
    pre_trade_day = _norm_day(pre_trade_day)
    print(f"[INFO] 使用交易日: {pre_trade_day}")

    pairs = bench_pairs_for_day(pre_trade_day)
    n_idx = len(BENCH_CODE_TO_RQ)
    n_fut = len(pairs) - n_idx
    print(f"[INFO] 标的: 指数 {n_idx} + 股指期货 {n_fut} = {len(pairs)}")

    rows = []
    for bench_code, rq_id in pairs:
        r = _bench_row(bench_code, rq_id, pre_trade_day)
        if r:
            rows.append(_df_nan_to_none(pd.DataFrame([r])).to_dict("records")[0])

    if not rows:
        print(
            f"[ERR] 基准数据为空（交易日={pre_trade_day}）。"
            "常见原因：该日不是 A 股交易日（周末/节假日）、"
            "或查询的是「今天」但日线尚未入库、"
            "或米筐侧该指数合约无行情；请换最近一个交易日再试。"
        )
        return False

    if dry_run:
        fut_list = [p[0] for p in pairs[n_idx:]]
        print(f"[DRY-RUN] 股指期货合约: {fut_list}")
        print(f"[DRY-RUN] 拉取成功 {len(rows)} / {len(pairs)} 条（以下摘要，不落库）")
        for rec in rows:
            print(
                f"  {rec.get('code')}\tclose={rec.get('close')}\t"
                f"pct_chg={rec.get('pct_chg')}\tcode_rq={rec.get('code_rq')}"
            )
        return True

    client = get_client()
    table = client[mongo_db][target_coll]
    for dv in _day_variants(pre_trade_day):
        table.delete_many({"date": dv})
    table.insert_many(rows, ordered=False)
    print(f"[OK] {mongo_db}.{target_coll} 写入 {len(rows)} 条 (date={pre_trade_day})")
    return True


def create_indexes_rq_bench(
    mongo_db: str = "basic_rq",
) -> None:
    """为 rq_bench 建 (date, code) 唯一索引。"""
    c = get_client()
    t = c[mongo_db]["rq_bench"]
    t.create_index([("date", pymongo.ASCENDING), ("code", pymongo.ASCENDING)], background=True, unique=True)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="更新 basic_rq.rq_bench（米筐行情）")
    p.add_argument(
        "pre_trade_day",
        nargs="?",
        default=None,
        help="交易日：写入该日行情；省略则先看环境变量 RQ_BENCH_PRE_DAY，再无则本年 4 月 14 日",
    )
    p.add_argument("--db", default="basic_rq", help="数据库名")
    p.add_argument("--index", action="store_true", help="仅建索引，不写数")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只拉行情并打印摘要，不写 Mongo、不建索引",
    )
    args = p.parse_args()

    if args.index:
        create_indexes_rq_bench(mongo_db=args.db)
        print("[OK] rq_bench 索引已处理")
    else:
        if args.pre_trade_day is not None:
            day = args.pre_trade_day
        elif os.environ.get("RQ_BENCH_PRE_DAY"):
            day = os.environ["RQ_BENCH_PRE_DAY"]
            print("ℹ️ 使用环境变量 RQ_BENCH_PRE_DAY 作为交易日")
        else:
            y = date.today().year
            day = date(y, 4, 14).strftime("%Y-%m-%d")
            print(f"ℹ️ 未传日期参数：使用本年 4 月 14 日作为交易日（{day}）")

        if args.dry_run:
            update_rq_bench(day, mongo_db=args.db, dry_run=True)
        else:
            create_indexes_rq_bench(mongo_db=args.db)
            update_rq_bench(day, mongo_db=args.db, dry_run=False)
