"""吾执量化精选一号 SASQ16：与零一号共用管理人「等6个产品净值表发送YYYYMMDD」邮件。"""
from __future__ import annotations

from django.conf import settings

from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, FundNavProduct


def get_lhjxyh_fund_product() -> FundNavProduct:
    key = getattr(settings, "NAV_REAL_WZ_LHJXYH_MASTER", "WZ_LHJXYH_MASTER")
    for f in FUND_NAV_PRODUCTS:
        if f["product_key"] == key:
            return f
    raise RuntimeError("未配置吾执量化精选一号 WZ_LHJXYH_MASTER 产品")
