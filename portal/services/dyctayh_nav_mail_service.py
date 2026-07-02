"""吾执多元尊选一号 SXE021(总)：fareport「【基金净值】…」主题邮件解析；附件为 B 类表时取母基金列。"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd
from django.conf import settings

from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, FundNavProduct
from portal.db.mongo import bson_safe_value
from portal.services.fund_nav_real_service import (
    _parse_date_to_iso,
    _parse_decimal,
    _read_fund_nav_dataframe,
)

_MASTER_HEADER_KEYS = {
    "母基金单位净值": "master_unit_nav",
    "母基金累计单位净值": "master_cumulative_unit_nav",
    "母基金资产净值": "master_net_asset_value",
    "母基金产品代码": "master_product_code",
    "母基金产品名称": "master_product_name",
}

_NAV_DATE_KEYS = {"日期", "净值日期", "估值日期"}


# B 类净值邮件主题（fareport 实际发件主题；落库仍取附件内母基金列）
DYZXYH_NAV_MAIL_SHARE_CLASS_CODE = "T06312(B级)"
DYZXYH_NAV_MAIL_B_CLASS_NAME = "吾执多元尊选一号私募证券投资基金B类"


def build_dyctayh_nav_mail_subject(nav_iso: str) -> str:
    """主题示例：【基金净值】T06312(B级)_吾执多元尊选一号私募证券投资基金B类_2026-06-30"""
    day = (nav_iso or "").strip()[:10]
    return (
        f"【基金净值】{DYZXYH_NAV_MAIL_SHARE_CLASS_CODE}_"
        f"{DYZXYH_NAV_MAIL_B_CLASS_NAME}_{day}"
    )


def get_dyzxyh_fund_product() -> FundNavProduct:
    key = getattr(settings, "NAV_REAL_WZ_DYZXYH_MASTER", "WZ_DYZXYH_MASTER")
    for f in FUND_NAV_PRODUCTS:
        if f["product_key"] == key:
            return f
    raise RuntimeError("未配置多元尊选一号 WZ_DYZXYH_MASTER 产品")


def get_dyctayh_fund_product() -> FundNavProduct:
    """兼容旧调用名。"""
    return get_dyzxyh_fund_product()


def pick_dyctayh_nav_excel(files: list) -> Any | None:
    """优先文件名含 SXE021 / 尊选 / 基金净值 的 Excel（含 T06312 B 类附件）。"""
    from pathlib import Path

    if not files:
        return None
    scored: list[tuple[int, Path]] = []
    for p in files:
        p = Path(p)
        if not p.is_file():
            continue
        name = p.name
        lower = name.lower()
        if not lower.endswith((".xlsx", ".xls", ".xlsm")):
            continue
        score = 0
        if "SXE021" in name.upper():
            score += 4
        if "尊选" in name:
            score += 4
        if "T06312" in name.upper():
            score += 2
        if "基金净值" in name:
            score += 2
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] > 0:
        return scored[0][1]
    excels = [
        Path(p)
        for p in files
        if Path(p).suffix.lower() in (".xlsx", ".xls", ".xlsm")
    ]
    return excels[0] if excels else None


def _normalize_header_cell(v: Any) -> str:
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    return re.sub(r"\s+", "", str(v).strip())


def _build_master_col_map(columns: list) -> dict[str, str]:
    col_map: dict[str, str] = {}
    for col in columns:
        key = _normalize_header_cell(col)
        if key in _NAV_DATE_KEYS:
            col_map.setdefault("nav_date", col)
        if key in _MASTER_HEADER_KEYS:
            col_map[_MASTER_HEADER_KEYS[key]] = col
    return col_map


def _cell_text(v: Any) -> str:
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none") else s


def _normalize_master_code(v: Any) -> str:
    s = _cell_text(v).upper().replace(" ", "")
    if s.endswith("(总)"):
        s = s[: -len("(总)")]
    return s


def parse_dyzxyh_master_nav_excel(
    file_bytes: bytes,
    *,
    filename: str,
    fund: FundNavProduct,
    expected_nav_iso: str,
) -> dict[str, Any]:
    """
    解析 B 类净值附件：忽略 T06312(B级) 行内份额列，取同行母基金单位/累计净值与母基金产品信息。
    """
    df = _read_fund_nav_dataframe(file_bytes, filename)
    if df.empty:
        raise ValueError("Excel 无数据行")

    col_map = _build_master_col_map(list(df.columns))
    need = (
        "nav_date",
        "master_unit_nav",
        "master_cumulative_unit_nav",
        "master_product_code",
        "master_product_name",
    )
    missing = [k for k in need if k not in col_map]
    if missing:
        raise ValueError(f"缺少母基金列: {', '.join(missing)}")

    exp = expected_nav_iso.strip()[:10]
    exp_master = _normalize_master_code(fund["asset_code"])

    row = None
    for i in range(len(df)):
        r = df.iloc[i]
        nav_try = _parse_date_to_iso(r[col_map["nav_date"]])
        if nav_try != exp:
            continue
        master_code = _normalize_master_code(r[col_map["master_product_code"]])
        if not master_code:
            continue
        if master_code != exp_master:
            continue
        unit = _parse_decimal(r[col_map["master_unit_nav"]])
        cum = _parse_decimal(r[col_map["master_cumulative_unit_nav"]])
        if unit is None or cum is None:
            continue
        row = r
        break

    if row is None:
        raise ValueError(
            f"未找到母基金数据行（需净值日 {exp}、母基金产品代码 {exp_master}，"
            "且含母基金单位/累计净值；B 类 T06312 份额列已忽略）"
        )

    nav_iso = _parse_date_to_iso(row[col_map["nav_date"]])
    asset_name = _cell_text(row[col_map["master_product_name"]]) or fund["name_prefix"]
    doc: dict[str, Any] = {
        "nav_date": nav_iso,
        "asset_code": str(fund["asset_code"]).strip(),
        "asset_name": asset_name,
        "unit_nav": _parse_decimal(row[col_map["master_unit_nav"]]),
        "cumulative_unit_nav": _parse_decimal(row[col_map["master_cumulative_unit_nav"]]),
    }
    if "master_net_asset_value" in col_map:
        nav_val = _parse_decimal(row[col_map["master_net_asset_value"]])
        if nav_val is not None:
            doc["net_asset_value"] = nav_val

    for k, v in list(doc.items()):
        doc[k] = bson_safe_value(v)
    return doc
