"""吾执零一号 STZ049：管理人「等6个产品净值表发送YYYYMMDD」主题 + 集合计划每日净值表。"""
from __future__ import annotations

from django.conf import settings

from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, FundNavProduct

# 主题末尾 8 位为净值批次日 YYYYMMDD（与 nav_date 一致）。内层引号为中文弯引号，须与邮箱主题完全一致。
_WZ_LYH_NAV_MAIL_SUBJECT_PREFIX = (
    "【净值表】上海吾执投资管理有限公司管理人旗下"
    "\u201c吾执安澜6号私募证券投资基金-STZ056\u201d等6个产品净值表发送"
)


def build_wz_lyh_nav_mail_subject(nav_iso: str) -> str:
    """示例：…发送20260511（nav_iso=2026-05-11）。"""
    day = (nav_iso or "").strip()[:10]
    ymd = day.replace("-", "")
    return f"{_WZ_LYH_NAV_MAIL_SUBJECT_PREFIX}{ymd}"


def build_wz_lyh_nav_mail_subject_variants(nav_iso: str) -> tuple[str, ...]:
    """主题完全匹配用：弯引号版 + 半角引号版（部分客户端/转发会改写引号）。"""
    primary = build_wz_lyh_nav_mail_subject(nav_iso)
    ascii_quotes = primary.replace("\u201c", '"').replace("\u201d", '"')
    out: list[str] = []
    for s in (primary, ascii_quotes):
        s = (s or "").strip()
        if s and s not in out:
            out.append(s)
    return tuple(out)


def get_wz_lyh_fund_product() -> FundNavProduct:
    key = getattr(settings, "NAV_REAL_WZ_LYH_MASTER", "WZ_LYH_MASTER")
    for f in FUND_NAV_PRODUCTS:
        if f["product_key"] == key:
            return f
    raise RuntimeError("未配置吾执零一号 WZ_LYH_MASTER 产品")


def get_wz_lyh_nav_mail_bundle_funds() -> tuple[FundNavProduct, FundNavProduct]:
    """同一封「等6个产品净值表」邮件：零一号 STZ049 + 量化精选一号 SASQ16。"""
    from portal.services.lhjxyh_fund_nav_mail_service import get_lhjxyh_fund_product

    return (get_wz_lyh_fund_product(), get_lhjxyh_fund_product())
