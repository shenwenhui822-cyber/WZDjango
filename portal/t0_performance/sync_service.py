"""从 FTP daily_report 拉取 *_wuzhi_日内交易汇总.xlsx 并写入 T0_performance.daily_report。"""
from __future__ import annotations

import ftplib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from io import BytesIO
from typing import Any

import pandas as pd
from django.conf import settings

from portal.db.mongo import bson_safe_value, get_mongo_client

from .date_iso import trade_date_to_iso

logger = logging.getLogger(__name__)

# Excel 列名（与导出表头一致）
COL_ACCOUNT = "账户名"
COL_DATE = "日期"
COL_MARKET_VALUE = "市值"
COL_NET = "日内交易净收益"
COL_TURNOVER = "日内买卖总额"
COL_UNCOVERED = "日内未平股数"
COL_COST = "日内交易总成本"


def _normalize_account(raw: Any) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if isinstance(raw, float):
        try:
            if raw == int(raw):
                return str(int(raw))
        except Exception:
            pass
    return str(raw).strip()


def parse_wuzhi_xlsx_bytes(filename: str, data: bytes) -> list[dict[str, Any]]:
    """解析单个 xlsx 字节流为待入库记录（不含 imported_at/source_file）。trade_date 为 YYYY-MM-DD。"""
    df = pd.read_excel(BytesIO(data), engine="openpyxl")
    if df.empty:
        return []

    cols = [str(c).strip() for c in df.columns]
    df.columns = cols
    missing = [
        COL_ACCOUNT,
        COL_DATE,
        COL_MARKET_VALUE,
        COL_NET,
        COL_TURNOVER,
        COL_UNCOVERED,
        COL_COST,
    ]
    for name in missing:
        if name not in df.columns:
            raise ValueError(f"表格缺少列「{name}」: {filename}，现有列={cols}")

    rows: list[dict[str, Any]] = []
    for _, sr in df.iterrows():
        acc = _normalize_account(sr.get(COL_ACCOUNT))
        td = trade_date_to_iso(sr.get(COL_DATE))
        if not acc or not td:
            continue
        row = {
            "account_name": acc,
            "trade_date": td,
            "market_value": bson_safe_value(sr.get(COL_MARKET_VALUE)),
            "intraday_net_profit": bson_safe_value(sr.get(COL_NET)),
            "intraday_turnover": bson_safe_value(sr.get(COL_TURNOVER)),
            "intraday_uncovered_shares": bson_safe_value(sr.get(COL_UNCOVERED)),
            "intraday_total_cost": bson_safe_value(sr.get(COL_COST)),
        }
        rows.append(row)
    return rows


@dataclass
class SyncResult:
    files_processed: int
    rows_upserted: int
    errors: list[str]


def sync_t0_from_ftp() -> SyncResult:
    """连接 FTP，筛选后缀匹配的文件，解析并 upsert 到 performance 集合。"""
    host = settings.T0_FTP_HOST
    port = settings.T0_FTP_PORT
    user = settings.T0_FTP_USER
    password = settings.T0_FTP_PASSWORD
    remote_dir = settings.T0_FTP_REMOTE_DIR
    suffix = getattr(settings, "T0_FTP_XLSX_SUFFIX", "_wuzhi_日内交易汇总.xlsx")

    errors: list[str] = []
    files_processed = 0
    rows_upserted = 0
    ftp: ftplib.FTP | None = None

    try:
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=90)
        ftp.login(user, password)
        ftp.set_pasv(True)
        ftp.cwd(remote_dir)

        try:
            names = ftp.nlst()
        except ftplib.error_perm as e:
            errors.append(f"列出目录失败 {remote_dir}: {e}")
            return SyncResult(0, 0, errors)

        targets = sorted(n for n in names if n.endswith(suffix))
        if not targets:
            errors.append(f"未找到以 {suffix!r} 结尾的文件")
            return SyncResult(0, 0, errors)

        mongo = get_mongo_client()
        try:
            coll = mongo[settings.MONGODB_T0_PERFORMANCE_DB][
                settings.MONGODB_T0_PERFORMANCE_COLLECTION
            ]
            coll.create_index(
                [("account_name", 1), ("trade_date", 1)],
                unique=True,
            )
            now = datetime.now(tz=dt_timezone.utc)

            for remote_name in targets:
                try:
                    buf = BytesIO()
                    ftp.retrbinary(f"RETR {remote_name}", buf.write)
                    buf.seek(0)
                    parsed = parse_wuzhi_xlsx_bytes(remote_name, buf.read())
                    if not parsed:
                        files_processed += 1
                        continue
                    for rec in parsed:
                        doc = {
                            **rec,
                            "source_file": remote_name,
                            "imported_at": now,
                        }
                        coll.update_one(
                            {
                                "account_name": doc["account_name"],
                                "trade_date": doc["trade_date"],
                            },
                            {"$set": doc},
                            upsert=True,
                        )
                        rows_upserted += 1
                    files_processed += 1
                except Exception as ex:
                    msg = f"{remote_name}: {ex}"
                    errors.append(msg)
                    logger.exception("T0 FTP 导入失败: %s", remote_name)
        finally:
            mongo.close()

    except Exception as ex:
        errors.append(str(ex))
        logger.exception("T0 FTP 同步失败")
    finally:
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass

    return SyncResult(files_processed, rows_upserted, errors)


def fetch_performance_rows(
    limit: int = 2500,
    account_name_filter: str | None = None,
) -> list[dict[str, Any]]:
    """按账户名升序、日期降序。不包含 source_file / imported_at。"""
    client = get_mongo_client()
    try:
        coll = client[settings.MONGODB_T0_PERFORMANCE_DB][
            settings.MONGODB_T0_PERFORMANCE_COLLECTION
        ]
        q: dict[str, Any] = {}
        if account_name_filter and account_name_filter.strip():
            q["account_name"] = account_name_filter.strip()
        cursor = coll.find(
            q,
            {"imported_at": 0, "_id": 0, "source_file": 0},
        ).sort([("account_name", 1), ("trade_date", -1)])
        items: list[dict[str, Any]] = []
        for doc in cursor:
            items.append(doc)
            if len(items) >= limit:
                break
        return items
    finally:
        client.close()


def distinct_performance_account_names() -> list[str]:
    client = get_mongo_client()
    try:
        coll = client[settings.MONGODB_T0_PERFORMANCE_DB][
            settings.MONGODB_T0_PERFORMANCE_COLLECTION
        ]
        raw = coll.distinct("account_name")
        return sorted(
            str(x).strip() for x in raw if x is not None and str(x).strip()
        )
    finally:
        client.close()
