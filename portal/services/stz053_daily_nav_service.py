"""吾执二二号 STZ053：fareport「发送每日净值信息」xls 中图二竖表解析，写入 fund_nav_real.WZ_EEH_MASTER。"""
from __future__ import annotations

import re
from io import BytesIO
from typing import Any

import pandas as pd
from django.conf import settings

from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, FundNavProduct
from portal.db.mongo import bson_safe_value
from portal.services.fund_nav_real_service import upsert_fund_nav_doc


def build_stz053_nav_mail_subject(ymd: str) -> str:
    """主题示例：【净值表】上海吾执投资管理有限公司吾执二二号产品净值表发送-管理人20260429"""
    d = (ymd or "").strip()
    if len(d) == 10 and d[4] == "-" and d[7] == "-":
        d = d.replace("-", "")[:8]
    return (
        "【净值表】上海吾执投资管理有限公司吾执二二号产品净值表发送-管理人"
        f"{d}"
    )


def nav_iso_from_stz053_filename(filename: str) -> str | None:
    m = re.search(r"(\d{4})年(\d{2})月(\d{2})日", filename or "")
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _parse_decimal(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "—", "－"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _sheet_names_priority(xl: pd.ExcelFile) -> list[str]:
    """优先含「图二」的工作表，其次第二张表，再其余顺序。"""
    names = list(xl.sheet_names)
    out: list[str] = []
    for n in names:
        if "图二" in n:
            out.append(n)
    if len(names) > 1:
        second = names[1]
        if second not in out:
            out.append(second)
    for n in names:
        if n not in out:
            out.append(n)
    return out


def _extract_stz053_vertical_block(df: pd.DataFrame) -> list[Any] | None:
    """任意列中自 STZ053 起连续 6 行。"""
    if df.empty:
        return None
    for col_idx in range(min(df.shape[1], 20)):
        col = df.iloc[:, col_idx]
        for row_idx in range(max(0, len(col) - 5)):
            cell0 = col.iloc[row_idx]
            if str(cell0).strip().upper() != "STZ053":
                continue
            block = [col.iloc[row_idx + i] for i in range(6)]
            return list(block)
    return None


def parse_stz053_figure2_xls(
    file_bytes: bytes,
    *,
    filename: str,
    expected_nav_iso: str,
    fund: FundNavProduct,
) -> dict[str, Any]:
    """解析附件中「图二」六行竖表：代码、名称、资产净值、总份额、份额净值、累计净值。"""
    exp = (expected_nav_iso or "").strip()[:10]
    file_day = nav_iso_from_stz053_filename(filename)
    if file_day and file_day != exp:
        raise ValueError(f"文件名日期 {file_day} 与目标净值日 {exp} 不一致")

    buf = BytesIO(file_bytes)
    xl = pd.ExcelFile(buf, engine="xlrd")

    last_err: Exception | None = None
    for sn in _sheet_names_priority(xl):
        try:
            df = pd.read_excel(xl, sheet_name=sn, header=None, engine="xlrd")
        except Exception as exc:
            last_err = exc
            continue
        block = _extract_stz053_vertical_block(df)
        if not block:
            continue
        code = str(block[0]).strip().upper()
        if code != fund["asset_code"].strip().upper():
            raise ValueError(f"资产代码 {code} 与期望 {fund['asset_code']} 不一致")
        name = str(block[1]).strip()
        if not name:
            raise ValueError("基金名称为空")

        doc: dict[str, Any] = {
            "nav_date": exp,
            "asset_code": fund["asset_code"],
            "asset_name": name,
            "net_asset_value": _parse_decimal(block[2]),
            "total_shares": _parse_decimal(block[3]),
            "unit_nav": _parse_decimal(block[4]),
            "cumulative_unit_nav": _parse_decimal(block[5]),
        }
        for k in ("unit_nav", "cumulative_unit_nav"):
            if doc.get(k) is None:
                raise ValueError(f"缺少必要数值字段: {k}")
        for k, v in list(doc.items()):
            doc[k] = bson_safe_value(v)
        return doc

    if last_err:
        raise ValueError(f"未找到图二 STZ053 六行竖表: {last_err}") from last_err
    raise ValueError("未找到图二 STZ053 六行竖表（请确认工作表内容与格式）")


def get_stz053_fund_product() -> FundNavProduct:
    key = getattr(settings, "NAV_REAL_WZ_EEH_MASTER", "WZ_EEH_MASTER")
    for f in FUND_NAV_PRODUCTS:
        if f["product_key"] == key and f.get("asset_code") == "STZ053":
            return f
    raise RuntimeError("未配置 STZ053 / WZ_EEH_MASTER 产品")


def import_stz053_nav_from_bytes(
    file_bytes: bytes,
    *,
    filename: str,
    expected_nav_iso: str,
    source_subject: str,
) -> dict[str, Any]:
    fund = get_stz053_fund_product()
    doc = parse_stz053_figure2_xls(
        file_bytes,
        filename=filename,
        expected_nav_iso=expected_nav_iso,
        fund=fund,
    )
    upsert_fund_nav_doc(doc, fund=fund, source_subject=source_subject)
    return doc
