"""吾执多元 CTA 一号 SXE021(总)：wangkan 邮箱「【基金净值】…多元CTA…」主题邮件解析辅助。"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings

from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, FundNavProduct


def build_dyctayh_nav_mail_subject(nav_iso: str) -> str:
    """主题示例：【基金净值】SXE021(总)_吾执多元CTA一号私募证券投资基金_2026-05-07"""
    day = (nav_iso or "").strip()[:10]
    return (
        "【基金净值】SXE021(总)_吾执多元CTA一号私募证券投资基金_"
        f"{day}"
    )


def get_dyctayh_fund_product() -> FundNavProduct:
    key = getattr(settings, "NAV_REAL_WZ_DYCTAYH_MASTER", "WZ_DYCTAYH_MASTER")
    for f in FUND_NAV_PRODUCTS:
        if f["product_key"] == key:
            return f
    raise RuntimeError("未配置多元 CTA 一号 WZ_DYCTAYH_MASTER 产品")


def pick_dyctayh_nav_excel(files: list[Path]) -> Path | None:
    """优先文件名含 SXE021 / 多元CTA / 基金净值 的 Excel。"""
    if not files:
        return None
    scored: list[tuple[int, Path]] = []
    for p in files:
        if not p.is_file():
            continue
        name = p.name
        lower = name.lower()
        if not lower.endswith((".xlsx", ".xls", ".xlsm")):
            continue
        score = 0
        if "SXE021" in name.upper():
            score += 4
        if "多元CTA" in name or "多元cta" in name.lower():
            score += 3
        if "基金净值" in name:
            score += 2
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] > 0:
        return scored[0][1]
    excels = [p for p in files if p.suffix.lower() in (".xlsx", ".xls", ".xlsm")]
    return excels[0] if excels else None
