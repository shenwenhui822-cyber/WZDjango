"""
泽鑫多维等五列净值 Excel（xlsx / xls）解析并写入 fund_nav_real.{WZ_ZXDW_MASTER|WZ_ZXDW_A|WZ_ZXDW_B|WZ_ZXDW_C}。

支持两种表头：
- 净值日期 / 累计净值（简表）
- 日期 / 累计单位净值（集合计划每日净值表等）
- 资产净值公告横表（基金代码行为 TZ051B/TZ051A/STZ051，净值在“基金份额净值/基金份额累计净值”行）

按产品代码后缀路由：以 A/B/C 结尾 -> 对应分集合；否则 -> WZ_ZXDW_MASTER（如 STZ049）。
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd
from django.conf import settings
from django.utils import timezone

from portal.db.mongo import bson_safe_value, get_fund_nav_zxdw_nav_collection

# 中文列名 -> 统一英文字段
_CANON_HEADERS: dict[str, str] = {
    "产品名称": "product_name",
    "产品代码": "asset_code",
    "净值日期": "nav_date",
    "日期": "nav_date",
    "单位净值": "unit_nav",
    "累计净值": "cumulative_nav",
    "累计单位净值": "cumulative_nav",
}


def zxdw_collection_for_asset_code(asset_code: str) -> str:
    """根据资产代码写入 settings.MONGODB_ZXDW_NAV_COLLECTIONS 中某一集合。"""
    code = (asset_code or "").strip().upper()
    if not code:
        raise ValueError("产品代码为空")
    allowed = getattr(settings, "MONGODB_ZXDW_NAV_COLLECTIONS", ())
    if code.endswith("C") and "WZ_ZXDW_C" in allowed:
        return "WZ_ZXDW_C"
    if code.endswith("B") and "WZ_ZXDW_B" in allowed:
        return "WZ_ZXDW_B"
    if code.endswith("A") and "WZ_ZXDW_A" in allowed:
        return "WZ_ZXDW_A"
    if "WZ_ZXDW_MASTER" in allowed:
        return "WZ_ZXDW_MASTER"
    return allowed[0] if allowed else "WZ_ZXDW_MASTER"


def _norm_header(h: Any) -> str:
    s = str(h).strip().replace("\n", "")
    return re.sub(r"\s+", " ", s)


def _parse_decimal(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip()
    if s in ("", "-", "—", "－"):
        return None
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date_to_iso(v: Any) -> str | None:
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except Exception:
        pass
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%Y-%m-%d")
        except Exception:
            pass
    s = str(v).strip()
    m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    if len(s) >= 10:
        s = s[:10]
    ts = pd.to_datetime(s, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


def _build_col_map(columns: list[Any]) -> dict[str, str]:
    """canonical_en -> DataFrame 列名原样（与 row[col] 一致；匹配用规范化表头）。"""
    out: dict[str, str] = {}
    seen_canon: set[str] = set()
    for c in columns:
        label = _norm_header(c)
        if label not in _CANON_HEADERS:
            continue
        canon = _CANON_HEADERS[label]
        if canon in seen_canon:
            continue
        seen_canon.add(canon)
        out[canon] = c
    return out


def _doc_from_row(row: Any, col_map: dict[str, str]) -> dict[str, Any] | None:
    nav_iso = _parse_date_to_iso(row[col_map["nav_date"]])
    if not nav_iso:
        return None
    code = str(row[col_map["asset_code"]]).strip()
    name = str(row[col_map["product_name"]]).strip()
    if not code:
        raise ValueError("产品代码为空")
    doc: dict[str, Any] = {
        "product_name": name,
        "asset_code": code,
        "nav_date": nav_iso,
        "unit_nav": _parse_decimal(row[col_map["unit_nav"]]),
        "cumulative_nav": _parse_decimal(row[col_map["cumulative_nav"]]),
    }
    for k, v in list(doc.items()):
        doc[k] = bson_safe_value(v)
    return doc


def load_zxdw_excel_dataframe(file_bytes: bytes, filename: str) -> Any:
    """
    读取 Excel；自动定位含「产品代码」的表头行（兼容标题行、日期说明行）。
    """
    _, ext = os.path.splitext((filename or "").lower())
    buf = BytesIO(file_bytes)
    engine = "xlrd" if ext == ".xls" else "openpyxl"
    raw = pd.read_excel(buf, header=None, engine=engine)
    if raw.empty:
        raise ValueError("Excel 无内容")

    header_idx: int | None = None
    for i in range(min(30, len(raw))):
        for j in range(raw.shape[1]):
            cell = raw.iat[i, j]
            if cell is None or (isinstance(cell, float) and pd.isna(cell)):
                continue
            if "产品代码" in str(cell).strip():
                header_idx = i
                break
        if header_idx is not None:
            break
    if header_idx is None:
        raise ValueError("未找到表头行（需含「产品代码」列）")

    headers = [
        _norm_header(raw.iat[header_idx, j]) if j < raw.shape[1] else ""
        for j in range(raw.shape[1])
    ]
    body = raw.iloc[header_idx + 1 :].copy()
    body.columns = headers[: body.shape[1]]
    body = body.dropna(how="all")
    return body


def _read_dataframe_simple(file_bytes: bytes, filename: str) -> Any:
    _, ext = os.path.splitext((filename or "").lower())
    buf = BytesIO(file_bytes)
    if ext == ".xls":
        return pd.read_excel(buf, header=0, engine="xlrd")
    return pd.read_excel(buf, header=0, engine="openpyxl")


def _read_raw_excel(file_bytes: bytes, filename: str) -> Any:
    _, ext = os.path.splitext((filename or "").lower())
    buf = BytesIO(file_bytes)
    engine = "xlrd" if ext == ".xls" else "openpyxl"
    return pd.read_excel(buf, header=None, engine=engine)


def _find_row_index_contains(raw: Any, keys: tuple[str, ...], max_rows: int = 40) -> int | None:
    rows = min(max_rows, len(raw))
    cols = raw.shape[1]
    for i in range(rows):
        for j in range(cols):
            v = raw.iat[i, j]
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            if any(k in s for k in keys):
                return i
    return None


def _extract_nav_date_iso_from_raw(raw: Any) -> str | None:
    rows = min(40, len(raw))
    cols = raw.shape[1]
    for i in range(rows):
        for j in range(cols):
            v = raw.iat[i, j]
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
            if m:
                y, mo, d = m.groups()
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
            if m:
                y, mo, d = m.groups()
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def _normalize_sheet_asset_code(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip().replace(" ", "")
    if s in ("", "-", "—", "－", "nan", "None"):
        return ""
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s.upper()


def _load_docs_from_wide_announcement(file_bytes: bytes, filename: str) -> list[dict[str, Any]]:
    """
    兼容“资产净值公告”横向格式：
    - 列方向是产品（TZ051B/TZ051A/STZ051）
    - 行方向是指标（基金代码/基金名称/基金份额净值/基金份额累计净值）
    """
    raw = _read_raw_excel(file_bytes, filename)
    if raw.empty:
        raise ValueError("Excel 无内容")

    nav_iso = _extract_nav_date_iso_from_raw(raw)
    if not nav_iso:
        raise ValueError("资产净值公告格式中未识别到净值日期")

    row_code = _find_row_index_contains(raw, ("基金代码", "产品代码"))
    row_name = _find_row_index_contains(raw, ("基金名称", "产品名称"))
    row_unit = _find_row_index_contains(raw, ("基金份额净值", "单位净值"))
    row_cum = _find_row_index_contains(raw, ("基金份额累计净值", "累计单位净值", "累计净值"))
    if row_code is None or row_cum is None:
        raise ValueError("资产净值公告格式缺少“基金代码/累计净值”关键行")

    docs: list[dict[str, Any]] = []
    for j in range(1, raw.shape[1]):
        code = _normalize_sheet_asset_code(raw.iat[row_code, j])
        if not code:
            continue
        name = ""
        if row_name is not None:
            name = str(raw.iat[row_name, j] or "").strip()
        unit_val = _parse_decimal(raw.iat[row_unit, j]) if row_unit is not None else None
        cum_val = _parse_decimal(raw.iat[row_cum, j])
        if unit_val is None and cum_val is None:
            continue
        doc: dict[str, Any] = {
            "product_name": name,
            "asset_code": code,
            "nav_date": nav_iso,
            "unit_nav": unit_val,
            "cumulative_nav": cum_val,
        }
        for k, v in list(doc.items()):
            doc[k] = bson_safe_value(v)
        docs.append(doc)

    if not docs:
        raise ValueError("资产净值公告格式未提取到产品净值数据")
    return docs


def import_zxdw_excel_all_rows(
    file_bytes: bytes,
    *,
    filename: str,
    collection_name: str,
    source_subject: str,
) -> dict[str, Any]:
    """多行历史表：逐行 upsert 到指定集合（本地目录导入用）。"""
    parsed_docs: list[dict[str, Any]] = []
    skipped = 0
    errors: list[str] = []
    try:
        df = load_zxdw_excel_dataframe(file_bytes, filename)
    except Exception:
        df = _read_dataframe_simple(file_bytes, filename)

    try:
        if df.empty:
            raise ValueError("Excel 无数据行")
        col_map = _build_col_map(list(df.columns))
        need_keys = ("product_name", "asset_code", "nav_date", "unit_nav", "cumulative_nav")
        for k in need_keys:
            if k not in col_map:
                cn_labels = [cn for cn, en in _CANON_HEADERS.items() if en == k]
                lab = cn_labels[0] if cn_labels else k
                raise ValueError(f"缺少列字段: {lab}")
        for i in range(len(df)):
            row = df.iloc[i]
            try:
                doc = _doc_from_row(row, col_map)
                if doc is None:
                    skipped += 1
                    continue
                parsed_docs.append(doc)
            except Exception as exc:
                errors.append(f"第{i + 1}行: {exc}")
    except Exception:
        # 回退：支持“资产净值公告”横向格式
        parsed_docs = _load_docs_from_wide_announcement(file_bytes, filename)

    upserted = 0
    for doc in parsed_docs:
        upsert_zxdw_nav_doc(doc, collection_name=collection_name, source_subject=source_subject)
        upserted += 1
    return {
        "upserted": upserted,
        "skipped_empty_date": skipped,
        "errors": errors,
    }


def import_zxdw_excel_routed_by_asset_code(
    file_bytes: bytes,
    *,
    filename: str,
    source_subject: str,
) -> dict[str, Any]:
    """解析整张表，按产品代码写入 MONGODB_ZXDW_NAV_COLLECTIONS 对应集合（邮件附件用）。"""
    parsed_docs: list[dict[str, Any]] = []
    skipped = 0
    errors: list[str] = []
    try:
        df = load_zxdw_excel_dataframe(file_bytes, filename)
    except Exception:
        df = _read_dataframe_simple(file_bytes, filename)

    try:
        if df.empty:
            raise ValueError("Excel 无数据行")
        col_map = _build_col_map(list(df.columns))
        need_keys = ("product_name", "asset_code", "nav_date", "unit_nav", "cumulative_nav")
        for k in need_keys:
            if k not in col_map:
                cn_labels = [cn for cn, en in _CANON_HEADERS.items() if en == k]
                lab = cn_labels[0] if cn_labels else k
                raise ValueError(f"缺少列字段: {lab}")
        for i in range(len(df)):
            row = df.iloc[i]
            try:
                doc = _doc_from_row(row, col_map)
                if doc is None:
                    skipped += 1
                    continue
                parsed_docs.append(doc)
            except Exception as exc:
                errors.append(f"第{i + 1}行: {exc}")
    except Exception:
        # 回退：支持“资产净值公告”横向格式
        parsed_docs = _load_docs_from_wide_announcement(file_bytes, filename)

    per_coll: dict[str, int] = {}
    for doc in parsed_docs:
        coll = zxdw_collection_for_asset_code(doc["asset_code"])
        upsert_zxdw_nav_doc(doc, collection_name=coll, source_subject=source_subject)
        per_coll[coll] = per_coll.get(coll, 0) + 1
    return {
        "upserted_total": sum(per_coll.values()),
        "per_collection": per_coll,
        "skipped_empty_date": skipped,
        "errors": errors,
    }


def upsert_zxdw_nav_doc(
    doc: dict[str, Any],
    *,
    collection_name: str,
    source_subject: str,
) -> None:
    coll = get_fund_nav_zxdw_nav_collection(collection_name)
    now = timezone.now()
    if isinstance(now, datetime) and timezone.is_naive(now):
        now = timezone.make_aware(now, timezone.get_current_timezone())

    payload = {
        **doc,
        "source_subject": source_subject,
        "updated_at": now,
    }
    coll.update_one(
        {"asset_code": doc["asset_code"], "nav_date": doc["nav_date"]},
        {"$set": payload},
        upsert=True,
    )
    try:
        coll.create_index(
            [("asset_code", 1), ("nav_date", 1)],
            unique=True,
            name="uniq_zxdw_asset_nav_date",
        )
    except Exception:
        pass
