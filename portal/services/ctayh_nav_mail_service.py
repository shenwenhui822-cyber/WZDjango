"""吾执 CTA 一号 SNG191：「净值表邮件…【国信托管】」主题与附件选取（收件箱为 ALPHA_MAIL_*）。"""
from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

from portal.data.fund_nav_real_config import FundNavProduct


def build_ctayh_nav_mail_subject(ymd: str) -> str:
    """主题示例：上海吾执投资管理有限公司净值表邮件20260507【国信托管】（日期为 YYYYMMDD）。"""
    y = (ymd or "").strip().replace("-", "")
    if len(y) >= 8:
        y = y[:8]
    return f"上海吾执投资管理有限公司净值表邮件{y}【国信托管】"


def get_ctayh_fund_product() -> FundNavProduct:
    return {
        "product_key": getattr(settings, "NAV_REAL_WZ_CTAYH_MASTER", "WZ_CTAYH_MASTER"),
        "name_prefix": "吾执CTA一号私募证券投资基金",
        "asset_code": "SNG191",
    }


def pick_ctayh_nav_excel(files: list[Path]) -> Path | None:
    """
    优先文件名含 SNG191 / 吾执CTA 的 xlsx/xls。

    压缩包内常有「主表 + A/B 子份额」多文件，文件名形如「…证券投资基金净值日期」与「…证券投资基金A净值日期」。
    评分相同时若按字符串排序，拉丁字母 A/B 会排在汉字「净」之前，易误选 A 类表（产品代码为 NG191A，非 SNG191）。
    故对「基金A净值」「基金B净值」子份额附件大幅降权。
    """
    excels = [
        p
        for p in files
        if p.is_file() and p.suffix.lower() in (".xlsx", ".xls", ".xlsm")
    ]
    if not excels:
        return None
    scored: list[tuple[int, Path]] = []
    for p in excels:
        name = p.name
        score = 0
        if "SNG191" in name.upper():
            score += 5
        if "吾执CTA" in name or "CTA一号" in name:
            score += 3
        if "净值" in name:
            score += 1
        # 国信托管批次里的 A/B 类单日表，非母基金 SNG191 行
        if re.search(r"基金[AB]净值", name):
            score -= 50
        scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], x[1].name))
    if scored[0][0] > 0:
        return scored[0][1]
    return excels[0]
