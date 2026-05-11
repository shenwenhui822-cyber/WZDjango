"""吾执三零号 SXN031(总)：wangkan 邮箱「基金净值」主题邮件解析辅助。"""
from __future__ import annotations

from django.conf import settings

from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, FundNavProduct


def build_slh_nav_mail_subject(nav_iso: str) -> str:
    """主题示例：【基金净值】SXN031(总)_吾执三零号私募证券投资基金_2026-05-06"""
    day = (nav_iso or "").strip()[:10]
    return (
        "【基金净值】SXN031(总)_吾执三零号私募证券投资基金_"
        f"{day}"
    )


def get_slh_fund_product() -> FundNavProduct:
    key = getattr(settings, "NAV_REAL_WZ_SLH_MASTER", "WZ_SLH_MASTER")
    for f in FUND_NAV_PRODUCTS:
        if f["product_key"] == key:
            return f
    raise RuntimeError("未配置吾执三零号 WZ_SLH_MASTER 产品")
