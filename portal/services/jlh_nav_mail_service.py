"""吾执九零号 SXR194：资产净值公告主题邮件（同表多份额时仅落库主代码 SXR194）。"""
from __future__ import annotations

from django.conf import settings

from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, FundNavProduct


def build_jlh_nav_mail_subject(nav_iso: str) -> str:
    """主题示例：资产净值公告_SXR194_吾执九零号私募证券投资基金_2026-05-07"""
    day = (nav_iso or "").strip()[:10]
    return f"资产净值公告_SXR194_吾执九零号私募证券投资基金_{day}"


def get_jlh_fund_product() -> FundNavProduct:
    key = getattr(settings, "NAV_REAL_WZ_JLH_MASTER", "WZ_JLH_MASTER")
    for f in FUND_NAV_PRODUCTS:
        if f["product_key"] == key:
            return f
    raise RuntimeError("未配置吾执九零号 WZ_JLH_MASTER 产品")
