"""博士一号真实净值 Excel（xlsx / xls）解析并写入 fund_nav_real.{WZ_BSYH_MASTER|WZ_BSYH_B}。"""
from __future__ import annotations

import os
import re
from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd
from django.utils import timezone

from portal.data.fund_nav_real_config import FundNavProduct
from portal.db.mongo import bson_safe_value, get_fund_nav_collection


# 表头（与 Excel 列名一致；部分导出带空格）
_HEADER_KEYS = {
    "日期": "nav_date",
    "净值日期": "nav_date",
    "资产代码": "asset_code",
    "产品代码": "asset_code",
    "资产名称": "asset_name",
    "产品名称": "asset_name",
    "资产份额净值(元)": "unit_nav",
    "资产份额净值 (元)": "unit_nav",
    "单位净值": "unit_nav",
    "资产份额累计净值(元)": "cumulative_unit_nav",
    "资产份额累计净值 (元)": "cumulative_unit_nav",
    "累计净值": "cumulative_unit_nav",
    "资产净值(元)": "net_asset_value",
    "资产净值 (元)": "net_asset_value",
    "基金资产净值": "net_asset_value",
    "总份额": "total_shares",
    "资产总值(元)": "total_asset_value",
    "资产总值 (元)": "total_asset_value",
    "实收资本(元)": "paid_in_capital",
    "实收资本 (元)": "paid_in_capital",
    "总资产(元)": "total_assets",
    "总资产 (元)": "total_assets",
    "持有份额": "shares_held",
    "参考市值(元)": "reference_market_value",
    "参考市值 (元)": "reference_market_value",
    # 净值序列导出（列名带「(元)」，单位净值与累计净值可能与上文并存，优先首列）
    "单位净值 (元)": "unit_nav",
    "单位净值(元)": "unit_nav",
    "累计净值 (元)": "cumulative_unit_nav",
    "累计净值(元)": "cumulative_unit_nav",
}


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
    if len(s) >= 10:
        s = s[:10]
    ts = pd.to_datetime(s, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


def _build_col_map(columns: list[Any]) -> dict[str, str]:
    """canonical_key -> DataFrame 列名原样（须与 row[col] / df[col] 一致；匹配用规范化表头）。"""
    out: dict[str, str] = {}
    seen_canon: set[str] = set()
    for c in columns:
        label = _norm_header(c)
        if label not in _HEADER_KEYS:
            continue
        canon = _HEADER_KEYS[label]
        if canon in seen_canon:
            continue
        seen_canon.add(canon)
        out[canon] = c
    return out


def _fund_nav_doc_from_row(
    row: Any,
    col_map: dict[str, str],
    fund: FundNavProduct,
) -> dict[str, Any] | None:
    """由一行生成文档；日期空则返回 None。"""
    nav_iso = _parse_date_to_iso(row[col_map["nav_date"]])
    if not nav_iso:
        return None
    raw_code = str(row[col_map["asset_code"]]).strip()
    expected = str(fund["asset_code"]).strip()
    if raw_code != expected:
        short = (
            expected.replace("(总)", "")
            .replace("（总）", "")
            .strip()
        )
        if raw_code != short:
            raise ValueError(
                f"资产代码 {raw_code} 与期望 {expected}（或简称 {short}）不一致"
            )
    code = expected

    doc: dict[str, Any] = {
        "nav_date": nav_iso,
        "asset_code": code,
        "asset_name": str(row[col_map["asset_name"]]).strip(),
        "unit_nav": _parse_decimal(row[col_map["unit_nav"]]),
        "cumulative_unit_nav": _parse_decimal(row[col_map["cumulative_unit_nav"]]),
    }
    if "net_asset_value" in col_map:
        doc["net_asset_value"] = _parse_decimal(row[col_map["net_asset_value"]])
    if "total_shares" in col_map:
        doc["total_shares"] = _parse_decimal(row[col_map["total_shares"]])
    if "total_asset_value" in col_map:
        doc["total_asset_value"] = _parse_decimal(row[col_map["total_asset_value"]])
    if "paid_in_capital" in col_map:
        doc["paid_in_capital"] = _parse_decimal(row[col_map["paid_in_capital"]])
    if "total_assets" in col_map:
        doc["total_assets"] = _parse_decimal(row[col_map["total_assets"]])
    if "shares_held" in col_map:
        doc["shares_held"] = _parse_decimal(row[col_map["shares_held"]])
    if "reference_market_value" in col_map:
        doc["reference_market_value"] = _parse_decimal(
            row[col_map["reference_market_value"]]
        )

    for k, v in list(doc.items()):
        doc[k] = bson_safe_value(v)
    return doc


def _read_fund_nav_dataframe(file_bytes: bytes, filename: str) -> Any:
    """按扩展名选择引擎：.xls 用 xlrd，其余用 openpyxl。"""
    _, ext = os.path.splitext((filename or "").lower())
    buf = BytesIO(file_bytes)
    if ext == ".xls":
        return pd.read_excel(buf, header=0, engine="xlrd")
    return pd.read_excel(buf, header=0, engine="openpyxl")


def parse_fund_nav_excel(
    file_bytes: bytes,
    *,
    filename: str,
    fund: FundNavProduct,
    expected_nav_iso: str,
) -> dict[str, Any]:
    df = _read_fund_nav_dataframe(file_bytes, filename)
    if df.empty:
        raise ValueError("Excel 无数据行")

    col_map = _build_col_map(list(df.columns))
    need_keys = ("nav_date", "asset_code", "asset_name", "unit_nav", "cumulative_unit_nav")
    for k in need_keys:
        if k not in col_map:
            raise ValueError(f"缺少列字段: {k}")

    row = None
    for i in range(len(df)):
        r = df.iloc[i]
        dcell = r[col_map["nav_date"]]
        try:
            if isinstance(dcell, float) and pd.isna(dcell):
                continue
        except Exception:
            pass
        if dcell is not None and str(dcell).strip() not in ("", "nan"):
            row = r
            break
    if row is None:
        raise ValueError("未找到有效数据行")

    nav_iso = _parse_date_to_iso(row[col_map["nav_date"]])
    if not nav_iso:
        raise ValueError("无法解析日期单元格")
    exp = expected_nav_iso.strip()[:10]
    if nav_iso != exp:
        raise ValueError(f"表格日期 {nav_iso} 与期望净值日 {exp} 不一致")

    doc = _fund_nav_doc_from_row(row, col_map, fund)
    if doc is None:
        raise ValueError("无法从首行生成文档")
    return doc


def import_fund_nav_excel_all_rows(
    file_bytes: bytes,
    *,
    filename: str,
    fund: FundNavProduct,
    source_subject: str,
) -> dict[str, Any]:
    """多行历史净值表：逐行 upsert；集合已按产品区分，同一集合内 nav_date 唯一。"""
    df = _read_fund_nav_dataframe(file_bytes, filename)
    if df.empty:
        raise ValueError("Excel 无数据行")

    col_map = _build_col_map(list(df.columns))
    need_keys = ("nav_date", "asset_code", "asset_name", "unit_nav", "cumulative_unit_nav")
    for k in need_keys:
        if k not in col_map:
            raise ValueError(f"缺少列字段: {k}")

    upserted = 0
    skipped = 0
    errors: list[str] = []
    for i in range(len(df)):
        row = df.iloc[i]
        try:
            doc = _fund_nav_doc_from_row(row, col_map, fund)
            if doc is None:
                skipped += 1
                continue
            upsert_fund_nav_doc(doc, fund=fund, source_subject=source_subject)
            upserted += 1
        except Exception as exc:
            errors.append(f"第{i + 2}行: {exc}")
    return {
        "upserted": upserted,
        "skipped_empty_date": skipped,
        "errors": errors,
    }


def parse_fund_nav_xlsx(
    file_bytes: bytes,
    *,
    fund: FundNavProduct,
    expected_nav_iso: str,
    filename: str = "nav.xlsx",
) -> dict[str, Any]:
    """兼容旧调用；请优先使用 parse_fund_nav_excel 并传入真实文件名。"""
    return parse_fund_nav_excel(
        file_bytes,
        filename=filename,
        fund=fund,
        expected_nav_iso=expected_nav_iso,
    )


def upsert_fund_nav_doc(
    doc: dict[str, Any],
    *,
    fund: FundNavProduct,
    source_subject: str,
) -> None:
    coll = get_fund_nav_collection(fund["product_key"])
    now = timezone.now()
    if isinstance(now, datetime) and timezone.is_naive(now):
        now = timezone.make_aware(now, timezone.get_current_timezone())

    payload = {
        **doc,
        "source_subject": source_subject,
        "updated_at": now,
    }
    coll.update_one(
        {"nav_date": doc["nav_date"]},
        {
            "$set": payload,
            "$unset": {
                "source_file": "",
                "report_date": "",
                "product_key": "",
                "_schema": "",
            },
        },
        upsert=True,
    )
    try:
        coll.create_index(
            [("nav_date", 1)],
            unique=True,
            name="uniq_fund_nav_nav_date",
        )
    except Exception:
        pass
