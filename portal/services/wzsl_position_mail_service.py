"""吾执三零号：fareport 东北证券对账单 RAR 内 xls「证券明细」→ position_fund_real.WZSL。"""
from __future__ import annotations

import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from portal.db.mongo import bson_safe_value
from portal.services.lhjx_position_mail_service import normalize_position_stock_code

# 邮件主题（与 fareport 完全一致，含 .rar 后缀；中间 8 位为持仓日 YYYYMMDD）
WZSL_POSITION_MAIL_SUBJECT_TEMPLATE = "18931015_18931015吾执三零号{ymd8}.rar"


def build_wzsl_position_mail_subject(ymd8: str) -> str:
    day = (ymd8 or "").strip().replace("-", "")[:8]
    return WZSL_POSITION_MAIL_SUBJECT_TEMPLATE.format(ymd8=day)


def _norm_cell(v: Any) -> str:
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    s = str(v).strip().replace("\n", " ")
    return re.sub(r"\s+", " ", s)


def _parse_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "—", "－", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find_dongbei_securities_detail_header_row(
    df: pd.DataFrame, *, max_scan: int = 50
) -> int | None:
    """东北证券对账单：定位「证券明细」表头行（含证券代码、当前数或持有数量）。"""
    n = min(max_scan, len(df))
    for i in range(n):
        parts: list[str] = []
        for j in range(min(df.shape[1], 24)):
            parts.append(_norm_cell(df.iat[i, j]))
        joined = " ".join(parts)
        if "证券代码" not in joined:
            continue
        if "当前数" in joined or "持有数量" in joined:
            return i
    return None


def parse_wzsl_position_excel(
    file_bytes: bytes,
    *,
    filename: str,
    position_date_iso: str,
) -> list[dict[str, Any]]:
    """
    解析东北证券总部对账单 xls/xlsx 中「证券明细」行；遇「流水明细」标题行即停止，
    不导入下方流水表。
    字段：date、code（SZ/SH/BJ+6 位）、position_size（当前数/股数）、position_value（市值）、
    avg_price（优先「成本价」，否则市值/数量）、last_price（「最新价」，可空）、
    market、name、source_file。
    """
    _, ext = os.path.splitext((filename or "").lower())
    engine = "xlrd" if ext == ".xls" else "openpyxl"
    buf = BytesIO(file_bytes)
    raw = pd.read_excel(buf, header=None, dtype=object, engine=engine)
    if raw.empty:
        raise ValueError("Excel 无内容")

    hdr_i = _find_dongbei_securities_detail_header_row(raw)
    if hdr_i is None:
        raise ValueError(
            "未解析到「证券明细」表头（需含证券代码、当前数或持有数量列）"
        )

    header_vals = [raw.iat[hdr_i, j] for j in range(raw.shape[1])]
    col_names: list[str] = []
    used: dict[str, int] = {}
    for j, v in enumerate(header_vals):
        base = _norm_cell(v) or f"_col{j}"
        if base in used:
            used[base] += 1
            base = f"{base}_{used[base]}"
        else:
            used[base] = 0
        col_names.append(base)

    body = raw.iloc[hdr_i + 1 :].copy()
    body.columns = col_names[: body.shape[1]]

    def _pick_col(candidates: tuple[str, ...]) -> str | None:
        for c in candidates:
            if c in body.columns:
                return c
        return None

    c_code = _pick_col(("证券代码",))
    c_qty = _pick_col(("当前数", "持有数量"))
    c_name = _pick_col(("股票名称", "证券简称"))
    c_mv = _pick_col(("市值",))
    c_mkt = _pick_col(("交易市场", "市场"))
    c_last = _pick_col(("最新价",))
    c_cost = _pick_col(("成本价",))

    if not c_code or not c_qty:
        raise ValueError("证券明细表缺少「证券代码」或「当前数/持有数量」列")

    pos_day = (position_date_iso or "").strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", pos_day):
        raise ValueError(f"position_date_iso 无效: {position_date_iso!r}")

    rows_out: list[dict[str, Any]] = []
    fname = os.path.basename(filename or "attachment.xls")

    for _, row in body.iterrows():
        row_joined = " ".join(
            _norm_cell(row.get(col)) for col in body.columns if col is not None
        )
        if "流水明细" in row_joined:
            break

        code_raw = row.get(c_code)
        if code_raw is None or (isinstance(code_raw, float) and pd.isna(code_raw)):
            continue
        s_code = str(code_raw).strip()
        if not s_code or s_code.lower() in ("nan",):
            continue
        if "合计" in s_code or s_code in ("-", "—"):
            continue

        mkt_cell = row.get(c_mkt) if c_mkt else ""
        unified = normalize_position_stock_code(mkt_cell, code_raw)
        if not unified:
            continue

        qty = _parse_float(row.get(c_qty))
        if qty is None or qty <= 0:
            continue

        mv = _parse_float(row.get(c_mv)) if c_mv else None
        last_price = _parse_float(row.get(c_last)) if c_last else None
        cost_price = _parse_float(row.get(c_cost)) if c_cost else None

        avg: float | None = None
        if cost_price is not None:
            avg = round(cost_price, 6)
        elif mv is not None and qty > 0:
            avg = round(mv / qty, 6)

        last_out: float | None = None
        if last_price is not None:
            last_out = round(last_price, 6)

        name = _norm_cell(row.get(c_name)) if c_name else ""
        if "合计" in name:
            continue

        doc: dict[str, Any] = {
            "date": pos_day,
            "code": unified,
            "position_size": qty,
            "position_value": mv,
            "avg_price": avg,
            "last_price": last_out,
            "market": _norm_cell(mkt_cell),
            "name": name,
            "source_file": fname,
        }
        for k, v in list(doc.items()):
            doc[k] = bson_safe_value(v)
        rows_out.append(doc)

    return rows_out


def pick_wzsl_xls_after_extract(extract_root: Path, ymd8: str) -> Path | None:
    """解压目录中优先选取吾执三零号对账单 xls/xlsx。"""
    files = [
        p
        for p in extract_root.rglob("*")
        if p.is_file() and p.suffix.lower() in (".xls", ".xlsx")
    ]
    if not files:
        return None
    scored: list[tuple[int, Path]] = []
    for p in files:
        name = p.name
        score = 0
        if "18931015" in name:
            score += 4
        if "吾执三零号" in name or "三零号" in name:
            score += 3
        if ymd8 in name.replace("-", ""):
            score += 2
        scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], len(x[1].name)))
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return files[0]
