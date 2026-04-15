"""
区间版：按日期区间更新 RQ 基准行情（rq_bench）。

特点：
1) 可自定义开始/结束日期（含端点）。
2) 默认仅处理交易日；是否为交易日通过 wzproject.settings 中：
   - MONGODB_DB_NAME
   - MONGODB_TRADE_CALENDAR_COLLECTION
   对应的 trade_calendar 集合判断。
3) 复用 update_rq_bench_17.py 的写库逻辑（字段、指数映射、索引等）。
4) 目标库/集合默认读取 settings：
   - MONGODB_RQ_BENCH_DB
   - MONGODB_RQ_BENCH_COLLECTION

用法（项目根目录）：
  python ./portal/management/update_rq_bench/update_rq_bench_range.py
  python ./portal/management/update_rq_bench/update_rq_bench_range.py 2026-04-01
  python ./portal/management/update_rq_bench/update_rq_bench_range.py 2026-04-01 2026-04-30
  python ./portal/management/update_rq_bench/update_rq_bench_range.py 2026-04-01 2026-04-30 --force-non-trading
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pandas as pd
import pymongo
from dotenv import load_dotenv

# 项目根目录（WZDjango）
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

load_dotenv(os.path.join(ROOT, ".env"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wzproject.settings")
import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from update_rq_bench_17 import create_indexes_rq_bench, update_rq_bench  # noqa: E402


def get_client() -> pymongo.MongoClient:
    env_uri = (os.getenv("MONGODB_URI") or "").strip()
    if not env_uri:
        raise RuntimeError("未配置 MONGODB_URI：请在项目根目录 .env 中设置。")
    return pymongo.MongoClient(env_uri)


def _norm_day(s: str) -> str:
    return pd.Timestamp(str(s).strip().replace("/", "-")).strftime("%Y-%m-%d")


def _iter_days(start_day: str, end_day: str) -> list[str]:
    s = pd.Timestamp(_norm_day(start_day))
    e = pd.Timestamp(_norm_day(end_day))
    if s > e:
        s, e = e, s
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(s, e, freq="D")]


def _is_trade_day(client: pymongo.MongoClient, day: str) -> bool:
    coll = client[settings.MONGODB_DB_NAME][settings.MONGODB_TRADE_CALENDAR_COLLECTION]
    iso = _norm_day(day)
    return coll.find_one({"trade_date": iso}, {"_id": 1}) is not None


def update_rq_bench_range(
    start_day: str,
    end_day: str,
    *,
    mongo_db: str | None = None,
    target_coll: str | None = None,
    force_non_trading: bool = False,
) -> dict[str, int]:
    mongo_db = (mongo_db or "").strip() or settings.MONGODB_RQ_BENCH_DB
    target_coll = (target_coll or "").strip() or settings.MONGODB_RQ_BENCH_COLLECTION

    days = _iter_days(start_day, end_day)
    print(f"[INFO] 区间: {days[0]} ~ {days[-1]} (共 {len(days)} 天)")
    print(
        "[INFO] 交易日判断来源: "
        f"{settings.MONGODB_DB_NAME}.{settings.MONGODB_TRADE_CALENDAR_COLLECTION}"
    )
    print(f"[INFO] 写入目标: {mongo_db}.{target_coll}")

    create_indexes_rq_bench(mongo_db=mongo_db)
    client = get_client()

    stats = {
        "total_days": len(days),
        "trading_days": 0,
        "written_days": 0,
        "failed_days": 0,
        "skipped_non_trading": 0,
    }
    for day in days:
        if (not force_non_trading) and (not _is_trade_day(client, day)):
            stats["skipped_non_trading"] += 1
            print(f"[SKIP] 非交易日跳过: {day}")
            continue

        stats["trading_days"] += 1
        ok = update_rq_bench(day, mongo_db=mongo_db, target_coll=target_coll)
        if ok:
            stats["written_days"] += 1
        else:
            stats["failed_days"] += 1

    print(
        "[OK] 完成: "
        f"总天数={stats['total_days']}, "
        f"交易日处理={stats['trading_days']}, "
        f"写入成功={stats['written_days']}, "
        f"写入失败={stats['failed_days']}, "
        f"跳过非交易日={stats['skipped_non_trading']}"
    )
    return stats


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="区间更新 rq_bench（按交易日过滤）")
    p.add_argument("start_day", nargs="?", default=None, help="开始日期 YYYY-MM-DD（含）")
    p.add_argument("end_day", nargs="?", default=None, help="结束日期 YYYY-MM-DD（含）；不传则同 start_day")
    p.add_argument("--db", default="", help="数据库名；不传则使用 settings.MONGODB_RQ_BENCH_DB")
    p.add_argument("--coll", default="", help="集合名；不传则使用 settings.MONGODB_RQ_BENCH_COLLECTION")
    p.add_argument("--force-non-trading", action="store_true", help="忽略交易日判断，区间内所有日期都尝试写入")
    args = p.parse_args()

    if args.start_day:
        start = args.start_day
    elif os.environ.get("RQ_BENCH_START_DAY"):
        start = os.environ["RQ_BENCH_START_DAY"]
        print("[INFO] 使用环境变量 RQ_BENCH_START_DAY 作为开始日期")
    elif os.environ.get("RQ_BENCH_PRE_DAY"):
        start = os.environ["RQ_BENCH_PRE_DAY"]
        print("[INFO] 使用环境变量 RQ_BENCH_PRE_DAY 作为开始日期")
    else:
        start = date.today().strftime("%Y-%m-%d")
        print(f"[INFO] 未传开始日期：默认使用今天 {start}")

    if args.end_day:
        end = args.end_day
    elif args.start_day:
        end = args.start_day
    elif os.environ.get("RQ_BENCH_END_DAY"):
        end = os.environ["RQ_BENCH_END_DAY"]
        print("[INFO] 使用环境变量 RQ_BENCH_END_DAY 作为结束日期")
    else:
        end = start

    update_rq_bench_range(
        start,
        end,
        mongo_db=args.db,
        target_coll=args.coll,
        force_non_trading=args.force_non_trading,
    )
