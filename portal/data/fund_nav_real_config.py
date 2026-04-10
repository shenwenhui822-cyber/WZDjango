"""博士一号真实净值：产品与邮件主题约定（同库 alpha_product / fund_nav_real）。"""
from __future__ import annotations

from typing import TypedDict


FUND_NAV_REAL_SCHEMA = "fund_nav_real"


class FundNavProduct(TypedDict):
    product_key: str
    name_prefix: str
    asset_code: str


# 与净值表文件名/邮件主题一致：{name_prefix}_{asset_code}_基金每日净值表YYYY-MM-DD
FUND_NAV_PRODUCTS: list[FundNavProduct] = [
    {
        "product_key": "WZ_BSYH_MASTER",
        "name_prefix": "吾执博士一号私募证券投资基金",
        "asset_code": "SBJP80",
    },
    {
        "product_key": "WZ_BSYH_B",
        "name_prefix": "吾执博士一号私募证券投资基金B类",
        "asset_code": "BJP80B",
    },
]


def build_fund_nav_mail_subject(fund: FundNavProduct, nav_date_iso: str) -> str:
    """例如：吾执博士一号私募证券投资基金_SBJP80_基金每日净值表2026-04-02"""
    day = (nav_date_iso or "").strip()[:10]
    return f"{fund['name_prefix']}_{fund['asset_code']}_基金每日净值表{day}"


def fund_by_asset_code(code: str) -> FundNavProduct | None:
    c = (code or "").strip()
    for f in FUND_NAV_PRODUCTS:
        if f["asset_code"] == c:
            return f
    return None


def fund_from_filename(filename: str) -> FundNavProduct | None:
    """根据附件/本地文件名粗判产品（BJP80B 优先于 SBJP80 子串匹配）。"""
    name = filename or ""
    if "BJP80B" in name.upper():
        return next((f for f in FUND_NAV_PRODUCTS if f["asset_code"] == "BJP80B"), None)
    if "SBJP80" in name.upper():
        return next((f for f in FUND_NAV_PRODUCTS if f["asset_code"] == "SBJP80"), None)
    return None
