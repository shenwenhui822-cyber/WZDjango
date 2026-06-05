"""Alpha 股票目标持仓：FTP /new_holding/<表名>/YYYYMMDD.csv → position_alpha_target.<表名>。"""
from __future__ import annotations

import ftplib
import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.utils import timezone

from portal.db.mongo import bson_safe_value, get_alpha_target_position_collection

logger = logging.getLogger(__name__)

_TABLE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


def validate_table_name(name: str) -> str | None:
    name = (name or "").strip()
    if _TABLE_NAME_RE.fullmatch(name):
        return name
    return None


def parse_alpha_target_csv_bytes(
    data: bytes, *, date_iso: str, source_label: str = ""
) -> list[dict]:
    """解析 CSV 字节流：列 ticker、lots，附加 date。"""
    label = source_label or "csv"
    last_err: Exception | None = None
    df = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            df = pd.read_csv(BytesIO(data), encoding=enc, dtype=str)
            break
        except Exception as exc:
            last_err = exc
    if df is None:
        raise RuntimeError(f"无法读取 CSV {label}: {last_err}")

    col_map = {str(c).strip().lower(): c for c in df.columns}
    ticker_key = col_map.get("ticker")
    lots_key = col_map.get("lots")
    if not ticker_key or not lots_key:
        raise ValueError(
            f"{label} 缺少 ticker/lots 列，当前列: {list(df.columns)}"
        )

    docs: list[dict] = []
    for _, row in df.iterrows():
        ticker_raw = row.get(ticker_key)
        if pd.isna(ticker_raw) or str(ticker_raw).strip() == "":
            continue
        ticker = str(ticker_raw).strip()
        lots_raw = row.get(lots_key)
        if pd.isna(lots_raw) or str(lots_raw).strip() == "":
            continue
        try:
            lots = float(str(lots_raw).replace(",", "").strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{label} ticker={ticker!r} lots 无法解析: {lots_raw!r}"
            ) from exc
        docs.append(
            {
                "date": date_iso,
                "ticker": ticker,
                "lots": bson_safe_value(lots),
            }
        )
    return [d for d in docs if bson_safe_value(d.get("ticker"))]


def parse_alpha_target_csv(path: Path, *, date_iso: str) -> list[dict]:
    return parse_alpha_target_csv_bytes(
        path.read_bytes(), date_iso=date_iso, source_label=path.name
    )


@dataclass
class AlphaTargetFtpImportResult:
    status: str
    position_date: str
    remote_dir: str = ""
    csv_name: str = ""
    folders_found: int = 0
    folders_imported: list[dict] = field(default_factory=list)
    folders_missing: list[str] = field(default_factory=list)
    rows_written: int = 0
    message: str = ""
    error: str = ""


def _connect_ftp() -> ftplib.FTP:
    host = settings.ALPHA_TARGET_FTP_HOST
    port = settings.ALPHA_TARGET_FTP_PORT
    user = settings.ALPHA_TARGET_FTP_USER
    password = settings.ALPHA_TARGET_FTP_PASSWORD
    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=90)
    ftp.login(user, password)
    ftp.set_pasv(True)
    return ftp


def _list_table_dirs(ftp: ftplib.FTP, remote_dir: str) -> list[str]:
    ftp.cwd(remote_dir)
    base = ftp.pwd()
    dirs: list[str] = []
    for name in sorted(ftp.nlst()):
        if name in (".", ".."):
            continue
        table = validate_table_name(name)
        if not table:
            continue
        try:
            ftp.cwd(name)
            ftp.cwd(base)
            dirs.append(table)
        except ftplib.error_perm:
            continue
    return dirs


def _download_csv_bytes(ftp: ftplib.FTP, remote_name: str) -> bytes | None:
    buf = BytesIO()
    try:
        ftp.retrbinary(f"RETR {remote_name}", buf.write)
    except ftplib.error_perm:
        return None
    data = buf.getvalue()
    return data if data else None


def _write_table(date_iso: str, table: str, docs: list[dict], now) -> int:
    for doc in docs:
        doc["updated_at"] = now
    coll = get_alpha_target_position_collection(table)
    coll.delete_many({"date": date_iso})
    if not docs:
        return 0
    coll.insert_many(docs)
    try:
        coll.create_index(
            [("date", 1), ("ticker", 1)],
            unique=True,
            name=f"uniq_{table}_date_ticker",
        )
    except Exception:
        pass
    return len(docs)


def import_alpha_target_from_ftp(*, date_iso: str) -> AlphaTargetFtpImportResult:
    """从 FTP 各子目录拉取当日 YYYYMMDD.csv 写入 position_alpha_target。"""
    remote_dir = settings.ALPHA_TARGET_FTP_REMOTE_DIR
    ymd8 = date_iso.replace("-", "")
    csv_name = f"{ymd8}.csv"
    now = timezone.now()

    result = AlphaTargetFtpImportResult(
        status="FAILED",
        position_date=date_iso,
        remote_dir=remote_dir,
        csv_name=csv_name,
    )
    ftp: ftplib.FTP | None = None
    try:
        ftp = _connect_ftp()
        tables = _list_table_dirs(ftp, remote_dir)
        result.folders_found = len(tables)
        if not tables:
            result.message = f"FTP {remote_dir} 下未找到有效表目录。"
            return result

        base = ftp.pwd()
        imported: list[dict] = []
        missing: list[str] = []
        total_rows = 0

        for table in tables:
            ftp.cwd(base)
            ftp.cwd(table)
            data = _download_csv_bytes(ftp, csv_name)
            if data is None:
                missing.append(table)
                continue
            docs = parse_alpha_target_csv_bytes(
                data, date_iso=date_iso, source_label=f"{table}/{csv_name}"
            )
            n = _write_table(date_iso, table, docs, now)
            total_rows += n
            imported.append({"table": table, "file": csv_name, "rows": n})

        result.folders_imported = imported
        result.folders_missing = missing
        result.rows_written = total_rows

        if not imported:
            result.message = (
                f"未导入任何表：FTP 各目录均无 {csv_name}。"
                f" 缺失目录: {', '.join(missing) or '无'}"
            )
            return result

        result.status = "SUCCESS"
        miss_note = ""
        if missing:
            miss_note = f"；以下目录无 {csv_name}: {', '.join(missing)}"
        result.message = (
            f"已从 FTP 写入 position_alpha_target 共 {len(imported)} 张表、"
            f"{total_rows} 条（date={date_iso}）{miss_note}"
        )
        return result
    except Exception as exc:
        logger.exception("Alpha 目标持仓 FTP 导入失败")
        result.error = str(exc)
        result.message = str(exc)
        return result
    finally:
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass
