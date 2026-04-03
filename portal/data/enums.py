"""
产品类型等枚举（写入 MongoDB 时使用 value 字符串，与 Excel/业务约定一致）。
可在此扩展新取值。
"""
from __future__ import annotations

from enum import Enum


class ProductType(str, Enum):
    """产品类型（示例：截图中为 long_only）。"""

    LONG_ONLY = "long_only"
    LONG_SHORT = "long_short"
    MARKET_NEUTRAL = "market_neutral"
    INDEX_ENHANCEMENT = "index_enhancement"
    QUANT_STOCK_PICK = "quant_stock_pick"
    OTHER = "other"


# 若 Excel 中为中文或其它写法，可映射到枚举 value；未命中则原样保存为字符串
PRODUCT_TYPE_TEXT_TO_VALUE: dict[str, str] = {
    "仅多头": ProductType.LONG_ONLY.value,
    "多头": ProductType.LONG_ONLY.value,
    "long only": ProductType.LONG_ONLY.value,
    "多空": ProductType.LONG_SHORT.value,
    "市场中性": ProductType.MARKET_NEUTRAL.value,
    "指增": ProductType.INDEX_ENHANCEMENT.value,
    "量化选股": ProductType.QUANT_STOCK_PICK.value,
}


def normalize_product_type(raw: object) -> str | None:
    """返回枚举 value 字符串，或无法识别时返回去空后的原字符串；空为 None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # 已是合法枚举值
    for e in ProductType:
        if e.value == s:
            return e.value
    low = s.lower()
    for e in ProductType:
        if e.value == low:
            return e.value
    if s in PRODUCT_TYPE_TEXT_TO_VALUE:
        return PRODUCT_TYPE_TEXT_TO_VALUE[s]
    if low in PRODUCT_TYPE_TEXT_TO_VALUE:
        return PRODUCT_TYPE_TEXT_TO_VALUE[low]
    # 未知类型：保留原文便于排查
    return s
