"""将 qichat 目录下周度绩效 CSV 导入 T0_performance.t0_order。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any

import pandas as pd
from django.conf import settings

from portal.db.mongo import bson_safe_value, get_mongo_client

from .date_iso import trade_date_to_iso

logger = logging.getLogger(__name__)

COL_DATE = "日期"
COL_PRODUCT = "产品名称"
COL_TOTAL_AMT = "总成交金额"
COL_ASSETS = "受托资产"
COL_PROFIT = "交易盈利"
COL_ANN = "年化收益率"
COL_TURNOVER = "换手率"


def _pct_cn_to_ratio(raw: Any) -> float | None:
    """「-6.54%」「109.61%」→ -0.0654 / 1.0961。"""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip().replace("%", "").replace("％", "")
    if not s:
        return None
    try:
        return float(s) / 100.0
    except ValueError:
        return None


def _read_csv(path: Path) -> pd.DataFrame:
    """依次尝试 UTF-8（含 BOM）与简体中文常见编码。"""
    last_err: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "gb2312"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError(f"无法读取 CSV: {path}")


def parse_qichat_csv(path: Path) -> list[dict[str, Any]]:
    df = _read_csv(path)
    if df.empty:
        return []
    df.columns = [str(c).strip() for c in df.columns]
    need = [COL_DATE, COL_PRODUCT, COL_TOTAL_AMT, COL_ASSETS, COL_PROFIT, COL_ANN, COL_TURNOVER]
    for c in need:
        if c not in df.columns:
            raise ValueError(f"{path.name} 缺少列「{c}」，当前列={list(df.columns)}")

    out: list[dict[str, Any]] = []
    for _, sr in df.iterrows():
        td = trade_date_to_iso(sr.get(COL_DATE))
        name = str(sr.get(COL_PRODUCT) or "").strip()
        if not td or not name:
            continue
        out.append(
            {
                "trade_date": td,
                "product_name": name,
                "total_transaction_amount": bson_safe_value(sr.get(COL_TOTAL_AMT)),
                "entrusted_assets": bson_safe_value(sr.get(COL_ASSETS)),
                "trading_profit": bson_safe_value(sr.get(COL_PROFIT)),
                "annualized_return": _pct_cn_to_ratio(sr.get(COL_ANN)),
                "turnover_ratio": _pct_cn_to_ratio(sr.get(COL_TURNOVER)),
            }
        )
    return out


@dataclass
class QichatImportResult:
    files_processed: int
    rows_upserted: int
    errors: list[str] = field(default_factory=list)


def _import_qichat_csv_paths(csv_files: list[Path]) -> QichatImportResult:
    """将给定 CSV 路径列表写入 t0_order。唯一键：(trade_date, product_name)。"""
    errors: list[str] = []
    files_processed = 0
    rows_upserted = 0
    if not csv_files:
        return QichatImportResult(0, 0, errors)

    mongo = get_mongo_client()
    try:
        coll = mongo[settings.MONGODB_T0_PERFORMANCE_DB][
            settings.MONGODB_T0_ORDER_COLLECTION
        ]
        coll.create_index(
            [("trade_date", 1), ("product_name", 1)],
            unique=True,
        )
        now = datetime.now(tz=dt_timezone.utc)

        for p in csv_files:
            try:
                if not p.is_file():
                    errors.append(f"{p.name}: 文件不存在")
                    continue
                rows = parse_qichat_csv(p)
                if not rows:
                    files_processed += 1
                    continue
                for rec in rows:
                    doc = {
                        **rec,
                        "source_file": p.name,
                        "imported_at": now,
                    }
                    coll.update_one(
                        {
                            "trade_date": doc["trade_date"],
                            "product_name": doc["product_name"],
                        },
                        {"$set": doc},
                        upsert=True,
                    )
                    rows_upserted += 1
                files_processed += 1
            except Exception as ex:
                errors.append(f"{p.name}: {ex}")
                logger.exception("qichat CSV 导入失败: %s", p)
    finally:
        mongo.close()

    return QichatImportResult(files_processed, rows_upserted, errors)


def import_qichat_csv_files(paths: list[Path] | None) -> QichatImportResult:
    """仅导入指定文件（如邮件新下载的附件），不扫描整个目录。"""
    if not paths:
        return QichatImportResult(0, 0, [])
    unique = sorted({Path(x).resolve() for x in paths})
    return _import_qichat_csv_paths(unique)


def import_qichat_csv_dir(
    directory: Path | None = None,
) -> QichatImportResult:
    """扫描目录内全部 .csv（吾执周度绩效），写入 t0_order。唯一键：(trade_date, product_name)。"""
    root = Path(directory or getattr(settings, "T0_QICHAT_IMPORT_DIR", settings.BASE_DIR / "qichat"))
    if not root.is_dir():
        return QichatImportResult(
            0, 0, [f"目录不存在: {root}"]
        )
    csv_files = sorted(root.glob("*.csv"))
    return _import_qichat_csv_paths(csv_files)


def fetch_t0_order_rows(
    limit: int = 2500,
    product_name_filter: str | None = None,
) -> list[dict[str, Any]]:
    """按产品名称升序、日期降序；附加 *_display 百分比文案。不返回 source_file。"""
    client = get_mongo_client()
    try:
        coll = client[settings.MONGODB_T0_PERFORMANCE_DB][
            settings.MONGODB_T0_ORDER_COLLECTION
        ]
        q: dict[str, Any] = {}
        if product_name_filter and product_name_filter.strip():
            q["product_name"] = product_name_filter.strip()
        cur = coll.find(
            q,
            {"imported_at": 0, "_id": 0, "source_file": 0},
        ).sort([("product_name", 1), ("trade_date", -1)])
        items: list[dict[str, Any]] = []
        for doc in cur:
            ar = doc.get("annualized_return")
            tr = doc.get("turnover_ratio")
            doc["annualized_return_display"] = (
                f"{float(ar) * 100:.2f}%" if ar is not None else "—"
            )
            doc["turnover_ratio_display"] = (
                f"{float(tr) * 100:.2f}%" if tr is not None else "—"
            )
            items.append(doc)
            if len(items) >= limit:
                break
        return items
    finally:
        client.close()


def distinct_t0_order_product_names() -> list[str]:
    client = get_mongo_client()
    try:
        coll = client[settings.MONGODB_T0_PERFORMANCE_DB][
            settings.MONGODB_T0_ORDER_COLLECTION
        ]
        raw = coll.distinct("product_name")
        return sorted(
            str(x).strip() for x in raw if x is not None and str(x).strip()
        )
    finally:
        client.close()
