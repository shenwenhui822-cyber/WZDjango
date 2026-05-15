"""吾执零零号 SNP584：资产净值公告主题邮件（同表 A/B 行产品代码可能同为 SNP584 时按产品名称/分级名称区分主基金与 A 类）。"""
from __future__ import annotations

from django.conf import settings

from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, FundNavProduct


def build_llh_nav_mail_subject(nav_iso: str) -> str:
    """主题示例：资产净值公告_SNP584_吾执零零号私募证券投资基金_2026-05-07"""
    day = (nav_iso or "").strip()[:10]
    return f"资产净值公告_SNP584_吾执零零号私募证券投资基金_{day}"


def get_llh_fund_product() -> FundNavProduct:
    key = getattr(settings, "NAV_REAL_WZ_LLH_MASTER", "WZ_LLH_MASTER")
    for f in FUND_NAV_PRODUCTS:
        if f["product_key"] == key:
            return f
    raise RuntimeError("未配置吾执零零号 WZ_LLH_MASTER 产品")


def get_llh_a_fund_product() -> FundNavProduct:
    key = getattr(settings, "NAV_REAL_WZ_LLH_A", "WZ_LLH_A")
    for f in FUND_NAV_PRODUCTS:
        if f["product_key"] == key:
            return f
    raise RuntimeError("未配置吾执零零号 A 类 WZ_LLH_A 产品")


def llh_nav_mail_import_products() -> list[FundNavProduct]:
    """同一封资产净值公告邮件：主基金 + A 类。"""
    return [get_llh_fund_product(), get_llh_a_fund_product()]
