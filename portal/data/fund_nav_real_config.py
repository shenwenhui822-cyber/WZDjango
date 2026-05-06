"""真实净值门户产品列表：博士一号（NAV_REAL_*）与泽鑫多维（MONGODB_ZXDW_NAV_COLLECTIONS）；库名 settings.MONGODB_FUND_NAV_REAL_DB。"""
from __future__ import annotations

from typing import TypedDict

from django.conf import settings


class FundNavProduct(TypedDict):
    product_key: str
    name_prefix: str
    asset_code: str


# 与净值表文件名/邮件主题一致：{name_prefix}_{asset_code}_基金每日净值表YYYY-MM-DD
# product_key：博士一号为 NAV_REAL_*；泽鑫多维为 settings.MONGODB_ZXDW_NAV_COLLECTIONS 集合名
_ZXDW_NAV_TITLE_SUFFIX: dict[str, str] = {
    "WZ_ZXDW_MASTER": "",
    "WZ_ZXDW_A": "A类",
    "WZ_ZXDW_B": "B类",
    "WZ_ZXDW_C": "C类",
}

_ZXDW_BASE_TITLE = "吾执泽鑫多维私募证券投资基金"


def _build_zxdw_fund_nav_products() -> list[FundNavProduct]:
    out: list[FundNavProduct] = []
    for coll in getattr(settings, "MONGODB_ZXDW_NAV_COLLECTIONS", ()):
        suf = _ZXDW_NAV_TITLE_SUFFIX.get(coll, "")
        name_prefix = _ZXDW_BASE_TITLE + suf if suf else _ZXDW_BASE_TITLE
        out.append(
            {
                "product_key": coll,
                "name_prefix": name_prefix,
                "asset_code": f"__ZXDW__{coll}",
            }
        )
    return out


FUND_NAV_PRODUCTS: list[FundNavProduct] = [
    {
        "product_key": settings.NAV_REAL_WZ_BSYH_MASTER,
        "name_prefix": "吾执博士一号私募证券投资基金",
        "asset_code": "SBJP80",
    },
    {
        "product_key": settings.NAV_REAL_WZ_BSYH_B,
        "name_prefix": "吾执博士一号私募证券投资基金B类",
        "asset_code": "BJP80B",
    },
    {
        "product_key": getattr(
            settings, "NAV_REAL_WZ_BSEE_MASTER", "WZ_BSEE_MASTER"
        ),
        "name_prefix": "吾执二二号私募证券投资基金",
        "asset_code": "STZ053",
    },
    *_build_zxdw_fund_nav_products(),
]


def fund_nav_products_for_mail_import() -> list[FundNavProduct]:
    """博士一号等「基金每日净值表」邮件任务使用；排除 fareport 专用 STZ053 邮件。"""
    skip = frozenset(
        {getattr(settings, "NAV_REAL_WZ_BSEE_MASTER", "WZ_BSEE_MASTER")}
    )
    return [f for f in FUND_NAV_PRODUCTS if f["product_key"] not in skip]


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


# 基金净值门户表格列：与 portal.services.fund_nav_real_service._HEADER_KEYS 中英字段一致（仅展示表内列）
FUND_NAV_PORTAL_COLUMNS: list[tuple[str, str]] = [
    ("日期", "nav_date"),
    ("资产代码", "asset_code"),
    ("资产名称", "asset_name"),
    ("资产份额净值(元)", "unit_nav"),
    ("资产份额累计净值(元)", "cumulative_unit_nav"),
    ("资产净值(元)", "net_asset_value"),
    ("总份额", "total_shares"),
    ("资产总值(元)", "total_asset_value"),
]


def fund_from_filename(filename: str) -> FundNavProduct | None:
    """根据附件/本地文件名粗判产品（BJP80B 优先于 SBJP80 子串匹配）。"""
    name = filename or ""
    if "BJP80B" in name.upper():
        return next((f for f in FUND_NAV_PRODUCTS if f["asset_code"] == "BJP80B"), None)
    if "SBJP80" in name.upper():
        return next((f for f in FUND_NAV_PRODUCTS if f["asset_code"] == "SBJP80"), None)
    return None
