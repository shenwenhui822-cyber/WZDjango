"""吾执一三号 SAHK33：资产净值公告主题邮件（同表多份额时仅落库主代码 SAHK33）。"""
from __future__ import annotations

from django.conf import settings

from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, FundNavProduct


def build_ysh_nav_mail_subject(nav_iso: str) -> str:
    """主题示例：资产净值公告_SAHK33_吾执一三号私募证券投资基金_2026-05-07"""
    day = (nav_iso or "").strip()[:10]
    return f"资产净值公告_SAHK33_吾执一三号私募证券投资基金_{day}"


def get_ysh_fund_product() -> FundNavProduct:
    key = getattr(settings, "NAV_REAL_WZ_YSH_MASTER", "WZ_YSH_MASTER")
    for f in FUND_NAV_PRODUCTS:
        if f["product_key"] == key:
            return f
    raise RuntimeError("未配置吾执一三号 WZ_YSH_MASTER 产品")
