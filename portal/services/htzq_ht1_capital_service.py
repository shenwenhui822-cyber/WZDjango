"""华泰 HT1 普通账单：首表「资金情况」数据行解析，写入 fstock_settle_real.HTZQ_666810103835。"""
from __future__ import annotations

import os
import re
from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd
from django.utils import timezone

from portal.db.mongo import bson_safe_value, get_wz_bsyh_htqh_capital_collection

# 首表资金情况表头（与导出列名一致）
_HEADER_TO_FIELD = {
    "客户代码": "client_code",
    "资产账户": "asset_account",
    "资金余额": "capital_balance",
    "可用余额": "available_balance",
    "资产市值": "asset_market_value",
    "总资产": "total_assets",
    "币种": "currency",
}


def _norm_cell(v: Any) -> str:
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip().replace("\n", "")


def _parse_number(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except Exception:
        pass
    s = _norm_cell(v)
    if s in ("", "-", "—", "－"):
        return None
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _row_values(row: Any) -> list[str]:
    out: list[str] = []
    for x in row:
        out.append(_norm_cell(x))
    return out


def _is_header_row(vals: list[str]) -> bool:
    joined = " ".join(vals)
    return "客户代码" in joined and "资产账户" in joined and "总资产" in joined


def _build_col_index_map(header_vals: list[str]) -> dict[str, int]:
    """字段名 -> 列下标（同名列取首次出现）。"""
    idx: dict[str, int] = {}
    for i, raw in enumerate(header_vals):
        label = raw.strip()
        if label not in _HEADER_TO_FIELD:
            continue
        field = _HEADER_TO_FIELD[label]
        if field not in idx:
            idx[field] = i
    need = {"client_code", "asset_account", "total_assets"}
    if not need.issubset(idx.keys()):
        raise ValueError(f"资金情况表头不完整，需要至少包含: {need}，当前列: {header_vals}")
    return idx


def _doc_from_data_row(
    data_vals: list[str],
    col_idx: dict[str, int],
    *,
    statement_date: str,
) -> dict[str, Any]:
    def cell(field: str) -> str:
        i = col_idx.get(field)
        if i is None or i >= len(data_vals):
            return ""
        return data_vals[i]

    doc: dict[str, Any] = {
        "statement_date": statement_date,
        "client_code": cell("client_code"),
        "asset_account": cell("asset_account"),
        "capital_balance": _parse_number(cell("capital_balance") or None),
        "available_balance": _parse_number(cell("available_balance") or None),
        "asset_market_value": _parse_number(cell("asset_market_value") or None),
        "total_assets": _parse_number(cell("total_assets") or None),
        "currency": cell("currency") or None,
    }
    for k, v in list(doc.items()):
        doc[k] = bson_safe_value(v)
    return doc


def parse_ht1_capital_first_sheet(
    file_bytes: bytes,
    *,
    filename: str,
    expected_statement_iso: str,
) -> dict[str, Any]:
    """
    读取第一个工作表，定位「资金情况」表头行（含 客户代码/资产账户/总资产），取下一行作为数据行。
    若未找到表头，则回退为固定第 8 行（1-based）为数据、第 7 行为表头（与当前华泰导出一致）。
    """
    _, ext = os.path.splitext((filename or "").lower())
    buf = BytesIO(file_bytes)
    if ext == ".xls":
        df = pd.read_excel(buf, sheet_name=0, header=None, engine="xlrd")
    else:
        df = pd.read_excel(buf, sheet_name=0, header=None, engine="openpyxl")

    if df.empty or len(df) < 2:
        raise ValueError("Excel 首个工作表无足够行")

    exp = (expected_statement_iso or "").strip()[:10]
    header_row_idx: int | None = None
    for i in range(len(df) - 1):
        vals = _row_values(df.iloc[i])
        if _is_header_row(vals):
            header_row_idx = i
            break

    if header_row_idx is None:
        # 1-based 行 7=表头、8=数据 → 0-based 索引 6、7
        if len(df) < 8:
            raise ValueError("未找到「资金情况」表头且行数不足 8 行")
        header_row_idx = 6
        data_row_idx = 7
    else:
        data_row_idx = header_row_idx + 1

    header_vals = _row_values(df.iloc[header_row_idx])
    data_vals = _row_values(df.iloc[data_row_idx])
    col_idx = _build_col_index_map(header_vals)
    doc = _doc_from_data_row(data_vals, col_idx, statement_date=exp)
    return doc


_FILENAME_YMD_RE = re.compile(r"普通账单_HT1_(\d{8})\.xlsx?$", re.IGNORECASE)


def statement_ymd_from_filename(filename: str) -> str | None:
    m = _FILENAME_YMD_RE.search(filename or "")
    if not m:
        return None
    return m.group(1)


def upsert_ht1_capital_doc(
    doc: dict[str, Any],
    *,
    source_subject: str,
    source_file: str,
) -> None:
    coll = get_wz_bsyh_htqh_capital_collection()
    now = timezone.now()
    if isinstance(now, datetime) and timezone.is_naive(now):
        now = timezone.make_aware(now, timezone.get_current_timezone())

    payload = {
        **doc,
        "source_subject": source_subject,
        "source_file": source_file,
        "updated_at": now,
    }
    coll.update_one(
        {"statement_date": doc["statement_date"]},
        {"$set": payload},
        upsert=True,
    )
    try:
        coll.create_index(
            [("statement_date", 1)],
            unique=True,
            name="uniq_ht1_capital_statement_date",
        )
    except Exception:
        pass
