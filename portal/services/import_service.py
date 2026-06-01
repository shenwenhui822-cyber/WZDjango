"""Alpha xlsx 导入服务（页面/API/管理命令复用）。"""
from __future__ import annotations

import re
from typing import Any, Callable

import pandas as pd

from portal.data import value_parsers as vp
from portal.data.alpha_daily_schema import (
    ALPHA_DAILY_SCHEMA,
    is_alpha_daily_sheet,
    sheet_df_to_alpha_daily_records,
)
from portal.db.mongo import bson_safe_value, get_app_collection


def _report_date_key(v: Any) -> str | None:
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    return s[:10] if len(s) >= 10 else (s or None)


def _alpha_daily_pair(rec: dict[str, Any]) -> tuple[str, str] | None:
    rd = _report_date_key(rec.get("report_date"))
    pn = rec.get("product_name")
    if rd is None or pn is None:
        return None
    name = str(pn).strip()
    if not name:
        return None
    return (rd, name)


def _check_alpha_daily_batch_unique(
    batch: list[dict[str, Any]], sheet_name: str
) -> None:
    """同一工作表内 (report_date, product_name) 不可重复。"""
    seen: set[tuple[str, str]] = set()
    for rec in batch:
        p = _alpha_daily_pair(rec)
        if not p:
            continue
        if p in seen:
            raise ValueError(
                f"导入失败：工作表「{sheet_name}」内存在重复的报表日期与产品名称："
                f"{p[0]} / {p[1]}"
            )
        seen.add(p)


def _upsert_alpha_daily_batch(coll: Any, batch: list[dict[str, Any]]) -> tuple[int, int]:
    inserted = 0
    updated = 0
    for rec in batch:
        p = _alpha_daily_pair(rec)
        if not p:
            continue
        rd, pn = p
        flt = {
            "_schema": ALPHA_DAILY_SCHEMA,
            "report_date": rd,
            "product_name": pn,
        }
        result = coll.replace_one(flt, rec, upsert=True)
        if result.upserted_id is not None:
            inserted += 1
        else:
            updated += 1
    return inserted, updated


def _report_date_from_xlsx_filename(filename: str) -> str | None:
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", filename)
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    return f"{y}-{mo}-{d}"


def _inject_report_date_alpha_daily(
    records: list[dict[str, Any]], source_filename: str
) -> None:
    day = _report_date_from_xlsx_filename(source_filename)
    if not day:
        return
    parsed = vp.parse_report_date(day)
    if parsed is None:
        return
    for rec in records:
        if rec.get("report_date") is None:
            rec["report_date"] = parsed


def _sheet_to_records_legacy(
    df: pd.DataFrame, source_file: str, sheet_name: str
) -> list[dict[str, Any]]:
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    records: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        item: dict[str, Any] = {
            "_source_file": source_file,
            "_sheet_name": sheet_name,
            "_row_index": int(idx),
        }
        for col in df.columns:
            item[col] = bson_safe_value(row[col])
        records.append(item)
    return records


def _sheet_to_records_auto(
    df: pd.DataFrame,
    source_file: str,
    sheet_name: str,
    *,
    alpha_daily_product_name_normalizer: Callable[[object], str | None] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if is_alpha_daily_sheet(df):
        batch = sheet_df_to_alpha_daily_records(
            df,
            source_file,
            sheet_name,
            product_name_normalizer=alpha_daily_product_name_normalizer,
        )
        _inject_report_date_alpha_daily(batch, source_file)
        return batch, "alpha_daily"
    return _sheet_to_records_legacy(df, source_file, sheet_name), "raw"


def import_excel_fileobj(
    fileobj: Any,
    source_filename: str,
    *,
    alpha_daily_product_name_normalizer: Callable[[object], str | None] | None = None,
) -> dict[str, Any]:
    xl = pd.ExcelFile(fileobj, engine="openpyxl")
    coll = get_app_collection()

    total_inserted = 0
    total_updated = 0
    sheet_stats: list[dict[str, Any]] = []
    for sheet_name in xl.sheet_names:
        df = xl.parse(sheet_name)
        if df.empty:
            sheet_stats.append(
                {"sheet": sheet_name, "inserted": 0, "schema": "empty", "skipped": True}
            )
            continue
        batch, schema_tag = _sheet_to_records_auto(
            df,
            source_filename,
            sheet_name,
            alpha_daily_product_name_normalizer=alpha_daily_product_name_normalizer,
        )
        if not batch:
            sheet_stats.append(
                {"sheet": sheet_name, "inserted": 0, "schema": schema_tag, "skipped": True}
            )
            continue
        if schema_tag == "alpha_daily":
            _check_alpha_daily_batch_unique(batch, sheet_name)
            inserted, updated = _upsert_alpha_daily_batch(coll, batch)
        else:
            coll.insert_many(batch, ordered=False)
            inserted, updated = len(batch), 0
        total_inserted += inserted
        total_updated += updated
        sheet_stats.append(
            {
                "sheet": sheet_name,
                "inserted": inserted,
                "updated": updated,
                "schema": schema_tag,
            }
        )

    return {
        "file": source_filename,
        "inserted": total_inserted,
        "updated": total_updated,
        "upserted": total_inserted + total_updated,
        "sheets": sheet_stats,
    }

