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
    "估值日期": "nav_date",
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
    "资产净值": "net_asset_value",
    "基金资产净值": "net_asset_value",
    "总份额": "total_shares",
    "资产份额": "total_shares",
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
    "累计单位净值": "cumulative_unit_nav",
    "单位净值(元/份)": "unit_nav",
    "累计单位净值(元/份)": "cumulative_unit_nav",
    "产品资产净值": "net_asset_value",
    "产品总份额": "total_shares",
}


def _normalize_fund_nav_asset_code_cell(v: Any) -> str:
    """Excel 中产品代码：去空格、统一大写；纯数字串形如 191.0 → 191（少见）。"""
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    s = str(v).strip().replace(" ", "")
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s.upper()


def _row_matches_fund_asset_code(row: Any, col_map: dict[str, str], fund: FundNavProduct) -> bool:
    """无产品代码列时保持原样（首行即主表）；有列时只处理与 fund 配置一致的那一行（如多份额同表只落库主代码）。"""
    if "asset_code" not in col_map:
        return True
    raw_code = _normalize_fund_nav_asset_code_cell(row[col_map["asset_code"]])
    expected = _normalize_fund_nav_asset_code_cell(fund["asset_code"])
    if raw_code == expected:
        return True
    short = expected.replace("(总)", "").replace("（总）", "").strip()
    return raw_code == short


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
    raw_code = _normalize_fund_nav_asset_code_cell(row[col_map["asset_code"]])
    expected = _normalize_fund_nav_asset_code_cell(fund["asset_code"])
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
    """
    读取净值表：默认尝试多行作为表头起始行（兼容首行为「产品基金净值数据」等合并标题，
    真实列名在第二行；亦兼容标准首行即表头）。
    """
    _, ext = os.path.splitext((filename or "").lower())
    buf = BytesIO(file_bytes)
    engine = "xlrd" if ext == ".xls" else "openpyxl"
    best_df: Any | None = None
    best_score = -1
    last_exc: Exception | None = None
    for header_idx in range(0, 12):
        buf.seek(0)
        try:
            df_try = pd.read_excel(buf, header=header_idx, engine=engine)
        except Exception as exc:
            last_exc = exc
            continue
        if df_try is None or getattr(df_try, "empty", True):
            continue
        col_map = _build_col_map(list(df_try.columns))
        if "nav_date" not in col_map:
            continue
        score = len(col_map)
        if score > best_score:
            best_score = score
            best_df = df_try
    if best_df is None:
        hint = (
            "无法识别净值表表头（需含「净值日期」或「日期」列）。"
            "若为顶部标题+第二行表头的模板，无需改文件，程序会自动识别。"
        )
        if last_exc:
            hint += f" 末次读取异常: {last_exc}"
        raise ValueError(hint)
    return best_df


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

    exp = expected_nav_iso.strip()[:10]
    exp_code = str(fund["asset_code"]).strip()
    row = None
    for i in range(len(df)):
        r = df.iloc[i]
        dcell = r[col_map["nav_date"]]
        try:
            if isinstance(dcell, float) and pd.isna(dcell):
                continue
        except Exception:
            pass
        if dcell is None or str(dcell).strip() in ("", "nan"):
            continue
        if not _row_matches_fund_asset_code(r, col_map, fund):
            continue
        nav_try = _parse_date_to_iso(r[col_map["nav_date"]])
        if nav_try != exp:
            continue
        row = r
        break
    if row is None:
        raise ValueError(
            f"未找到有效数据行（需产品代码为 {exp_code} 且净值日为 {exp}）"
        )

    nav_iso = _parse_date_to_iso(row[col_map["nav_date"]])
    if not nav_iso:
        raise ValueError("无法解析日期单元格")

    doc = _fund_nav_doc_from_row(row, col_map, fund)
    if doc is None:
        raise ValueError("无法从匹配行生成文档")
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
        if not _row_matches_fund_asset_code(row, col_map, fund):
            continue
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
