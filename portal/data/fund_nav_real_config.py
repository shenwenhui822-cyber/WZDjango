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


# 侧栏隐藏的子份额：名称中含「A类」「B类」「C类」（博士一号 B、泽鑫多维 A/B/C 等）
_SHARE_CLASS_MARKERS: tuple[str, ...] = ("A类", "B类", "C类")


def is_fund_nav_share_class_product(fund: FundNavProduct) -> bool:
    return any(m in fund["name_prefix"] for m in _SHARE_CLASS_MARKERS)


def fund_nav_portal_sidebar_products() -> list[FundNavProduct]:
    """基金净值页左侧：仅主份额，单层单选（不含 A/B/C 子份额）。"""
    return [f for f in FUND_NAV_PRODUCTS if not is_fund_nav_share_class_product(f)]


def fund_nav_portal_sidebar_allowed_keys() -> frozenset[str]:
    return frozenset(f["product_key"] for f in fund_nav_portal_sidebar_products())


# 门户/曲线等产品下拉展示顺序（约）：三零号 → 多元量选 → 二二号 → 博士一号 → 多元一号 → 泽鑫多维 → 一三号 → CTA一号 → 九零号。
# 「量化精选一号/二号」「吾执零零号/零一号/一零号」尚无 NAV_REAL 配置项，待接入时在下列对应位置插入 FundNavProduct。
FUND_NAV_PRODUCTS: list[FundNavProduct] = [
    {
        "product_key": getattr(
            settings, "NAV_REAL_WZ_SLH_MASTER", "WZ_SLH_MASTER"
        ),
        "name_prefix": "吾执三零号私募证券投资基金",
        "asset_code": "SXN031(总)",
    },
    {
        "product_key": getattr(
            settings, "NAV_REAL_WZ_DYLX_MASTER", "WZ_DYLX_MASTER"
        ),
        "name_prefix": "吾执多元量选私募证券投资基金",
        "asset_code": "SAJM64(总)",
    },
    {
        "product_key": getattr(
            settings, "NAV_REAL_WZ_EEH_MASTER", "WZ_EEH_MASTER"
        ),
        "name_prefix": "吾执二二号私募证券投资基金",
        "asset_code": "STZ053",
    },
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
            settings, "NAV_REAL_WZ_DYYH_MASTER", "WZ_DYYH_MASTER"
        ),
        "name_prefix": "吾执多元一号私募证券投资基金",
        "asset_code": "SAJM63(总)",
    },
    *_build_zxdw_fund_nav_products(),
    {
        "product_key": getattr(
            settings, "NAV_REAL_WZ_YSH_MASTER", "WZ_YSH_MASTER"
        ),
        "name_prefix": "吾执一三号私募证券投资基金",
        "asset_code": "SAHK33",
    },
    {
        "product_key": getattr(
            settings, "NAV_REAL_WZ_JLH_MASTER", "WZ_JLH_MASTER"
        ),
        "name_prefix": "吾执九零号私募证券投资基金",
        "asset_code": "SXR194",
    },
    {
        "product_key": getattr(
            settings, "NAV_REAL_WZ_CTAYH_MASTER", "WZ_CTAYH_MASTER"
        ),
        "name_prefix": "吾执CTA一号私募证券投资基金",
        "asset_code": "SNG191",
    },
]


def fund_nav_products_for_mail_import() -> list[FundNavProduct]:
    """博士一号等「基金每日净值表」邮件任务使用；排除专用主题邮件（STZ053、多元一号、三零号、多元量选等）。"""
    skip = frozenset(
        {
            getattr(settings, "NAV_REAL_WZ_EEH_MASTER", "WZ_EEH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_DYYH_MASTER", "WZ_DYYH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_SLH_MASTER", "WZ_SLH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_DYLX_MASTER", "WZ_DYLX_MASTER"),
            getattr(settings, "NAV_REAL_WZ_YSH_MASTER", "WZ_YSH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_JLH_MASTER", "WZ_JLH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_CTAYH_MASTER", "WZ_CTAYH_MASTER"),
        }
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
    ("实收资本(元)", "paid_in_capital"),
    ("总资产(元)", "total_assets"),
]


def fund_from_filename(filename: str) -> FundNavProduct | None:
    """根据附件/本地文件名粗判产品（BJP80B 优先于 SBJP80 子串匹配）。"""
    name = filename or ""
    if "BJP80B" in name.upper():
        return next((f for f in FUND_NAV_PRODUCTS if f["asset_code"] == "BJP80B"), None)
    if "SBJP80" in name.upper():
        return next((f for f in FUND_NAV_PRODUCTS if f["asset_code"] == "SBJP80"), None)
    return None
