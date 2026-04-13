"""Alpha xlsx 导入服务（页面/API/管理命令复用）。"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from portal.data import value_parsers as vp
from portal.data.alpha_daily_schema import (
    ALPHA_DAILY_SCHEMA,
    is_alpha_daily_product_name_excluded,
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


def _check_alpha_daily_duplicates(
    coll: Any, batch: list[dict[str, Any]], sheet_name: str
) -> None:
    pairs: list[tuple[str, str]] = []
    for rec in batch:
        p = _alpha_daily_pair(rec)
        if p:
            pairs.append(p)

    seen: set[tuple[str, str]] = set()
    for p in pairs:
        if p in seen:
            raise ValueError(
                f"导入失败：工作表「{sheet_name}」内存在重复的报表日期与产品名称："
                f"{p[0]} / {p[1]}"
            )
        seen.add(p)

    if not seen:
        return

    pnames = list({p[1] for p in seen})
    existing: set[tuple[str, str]] = set()
    for doc in coll.find(
        {"_schema": ALPHA_DAILY_SCHEMA, "product_name": {"$in": pnames}},
        {"report_date": 1, "product_name": 1, "_id": 0},
    ):
        ep = _alpha_daily_pair(doc)
        if ep:
            existing.add(ep)

    conflict = seen & existing
    if conflict:
        rd, name = sorted(conflict)[0]
        raise ValueError(
            f"导入失败：报表日期 {rd} 与产品名称「{name}」已在库中存在，不可重复导入。"
        )


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
    df: pd.DataFrame, source_file: str, sheet_name: str
) -> tuple[list[dict[str, Any]], str]:
    if is_alpha_daily_sheet(df):
        batch = sheet_df_to_alpha_daily_records(df, source_file, sheet_name)
        _inject_report_date_alpha_daily(batch, source_file)
        return batch, "alpha_daily"
    return _sheet_to_records_legacy(df, source_file, sheet_name), "raw"


def import_excel_fileobj(fileobj: Any, source_filename: str) -> dict[str, Any]:
    xl = pd.ExcelFile(fileobj, engine="openpyxl")
    coll = get_app_collection()

    total_docs = 0
    sheet_stats: list[dict[str, Any]] = []
    for sheet_name in xl.sheet_names:
        df = xl.parse(sheet_name)
        if df.empty:
            sheet_stats.append(
                {"sheet": sheet_name, "inserted": 0, "schema": "empty", "skipped": True}
            )
            continue
        batch, schema_tag = _sheet_to_records_auto(df, source_filename, sheet_name)
        if not batch:
            sheet_stats.append(
                {"sheet": sheet_name, "inserted": 0, "schema": schema_tag, "skipped": True}
            )
            continue
        skipped_excluded = 0
        if schema_tag == "alpha_daily":
            n_before = len(batch)
            batch = [
                r
                for r in batch
                if not is_alpha_daily_product_name_excluded(r.get("product_name"))
            ]
            skipped_excluded = n_before - len(batch)
            if not batch:
                sheet_stats.append(
                    {
                        "sheet": sheet_name,
                        "inserted": 0,
                        "schema": schema_tag,
                        "skipped": True,
                        "skipped_excluded_name_prefix": skipped_excluded,
                    }
                )
                continue
            _check_alpha_daily_duplicates(coll, batch, sheet_name)
        coll.insert_many(batch, ordered=False)
        n = len(batch)
        total_docs += n
        row: dict[str, Any] = {"sheet": sheet_name, "inserted": n, "schema": schema_tag}
        if skipped_excluded:
            row["skipped_excluded_name_prefix"] = skipped_excluded
        sheet_stats.append(row)

    return {"file": source_filename, "inserted": total_docs, "sheets": sheet_stats}

