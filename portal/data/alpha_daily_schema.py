"""
Alpha 产品日报：中文表头 -> 英文字段、按类型解析。

识别规则：表头含「当日盈亏比例」「产品类型」「总盈亏金额」，则按本 schema 导入（英文字段）。
"""
from __future__ import annotations

import re
from typing import Any, Callable

import pandas as pd

from portal.data import value_parsers as vp

# (中文列名, MongoDB 英文字段) — 与 Excel 表头一致（25 列）
ALPHA_DAILY_COLUMNS: list[tuple[str, str]] = [
    ("报表日期", "report_date"),
    ("产品名称", "product_name"),
    ("当日盈亏比例", "daily_pnl_ratio"),
    ("周度盈亏比例", "weekly_pnl_ratio"),
    ("月度盈亏比例", "monthly_pnl_ratio"),
    ("年度盈亏比例", "yearly_pnl_ratio"),
    ("当日超额比例", "daily_excess_return_ratio"),
    ("周度超额比例", "weekly_excess_return_ratio"),
    ("月度超额比例", "monthly_excess_return_ratio"),
    ("年度超额比例", "yearly_excess_return_ratio"),
    ("当前净值", "current_nav"),
    ("当前超额净值", "current_excess_nav"),
    ("当前净值回撤", "current_nav_drawdown"),
    ("最大净值回撤", "max_nav_drawdown"),
    ("当前超额净值回撤", "current_excess_nav_drawdown"),
    ("最大超额净值回撤", "max_excess_nav_drawdown"),
    ("多空暴露比例", "long_short_exposure_ratio"),
    ("换手率", "turnover_rate"),
    ("交易滑点", "trading_slippage"),
    ("资产总额", "total_assets"),
    ("总盈亏金额", "total_pnl_amount"),
    ("多头持仓市值", "long_position_mkt_value"),
    ("空头持仓市值", "short_position_mkt_value"),
    ("多头账户现金", "long_account_cash"),
    ("产品类型", "product_type"),
]

# 与「交易滑点」同一英文字段（表头变体）
HEADER_ALIASES: list[tuple[str, str]] = [
    ("交易滑点(bps)", "trading_slippage"),
    ("交易滑点（bps）", "trading_slippage"),
]

FIELD_PARSERS: dict[str, Callable[..., object]] = {
    "report_date": vp.parse_report_date,
    "product_name": vp.parse_text,
    "daily_pnl_ratio": vp.parse_percentage,
    "weekly_pnl_ratio": vp.parse_percentage,
    "monthly_pnl_ratio": vp.parse_percentage,
    "yearly_pnl_ratio": vp.parse_percentage,
    "daily_excess_return_ratio": vp.parse_percentage,
    "weekly_excess_return_ratio": vp.parse_percentage,
    "monthly_excess_return_ratio": vp.parse_percentage,
    "yearly_excess_return_ratio": vp.parse_percentage,
    "current_nav": vp.parse_float_amount,
    "current_excess_nav": vp.parse_float_amount,
    "current_nav_drawdown": vp.parse_percentage,
    "max_nav_drawdown": vp.parse_percentage,
    "current_excess_nav_drawdown": vp.parse_percentage,
    "max_excess_nav_drawdown": vp.parse_percentage,
    "long_short_exposure_ratio": vp.parse_percentage,
    "turnover_rate": vp.parse_percentage,
    "trading_slippage": vp.parse_float_amount,
    "total_assets": vp.parse_float_amount,
    "total_pnl_amount": vp.parse_float_amount,
    "long_position_mkt_value": vp.parse_float_amount,
    "short_position_mkt_value": vp.parse_float_amount,
    "long_account_cash": vp.parse_float_amount,
    "product_type": vp.parse_product_type,
}

ALPHA_DAILY_SCHEMA = "alpha_daily"

# 导入可入库、但列表/API/净值曲线不展示的产品名称前缀（可配置多个）
ALPHA_DAILY_EXCLUDED_PRODUCT_NAME_PREFIX = ["吾执", "双创选股多策略一号","多元量选一号-东吴","尊选多策略一号","江海远山-","稳健量选一号", "量化精选一号-","量化精选二号-光大","量化选股多策略","银河DMA"]


def is_alpha_daily_product_name_excluded(product_name: object) -> bool:
    """是否属于门户隐藏产品（名称以排除前缀开头）。"""
    if product_name is None:
        return False
    s = str(product_name).strip()
    if not s:
        return False
    prefixes = [
        str(x).strip()
        for x in ALPHA_DAILY_EXCLUDED_PRODUCT_NAME_PREFIX
        if str(x).strip()
    ]
    return any(s.startswith(p) for p in prefixes)


def alpha_daily_mongo_exclude_excluded_product_names() -> dict[str, Any]:
    """Mongo 查询片段：排除 product_name 以 ``ALPHA_DAILY_EXCLUDED_PRODUCT_NAME_PREFIX`` 开头的文档。"""
    prefixes = [
        str(x).strip()
        for x in ALPHA_DAILY_EXCLUDED_PRODUCT_NAME_PREFIX
        if str(x).strip()
    ]
    if not prefixes:
        return {}
    return {
        "$nor": [
            {"product_name": {"$regex": f"^{re.escape(p)}"}}
            for p in prefixes
        ]
    }


def normalize_header_cn(s: str) -> str:
    """表头规范化：去空白、全角空格，括号统一为半角，便于匹配。"""
    t = str(s).strip().replace("\u3000", " ")
    t = re.sub(r"\s+", "", t)
    t = t.replace("（", "(").replace("）", ")")
    return t


def _build_canonical_to_en() -> dict[str, str]:
    m: dict[str, str] = {}
    for cn, en in ALPHA_DAILY_COLUMNS:
        m[normalize_header_cn(cn)] = en
    for cn, en in HEADER_ALIASES:
        m[normalize_header_cn(cn)] = en
    return m


CANONICAL_HEADER_TO_EN: dict[str, str] = _build_canonical_to_en()


def is_alpha_daily_sheet(df: pd.DataFrame) -> bool:
    """与 Alpha 汇总表匹配（当前模板含报表日期、总盈亏金额等列）。"""
    cols = {normalize_header_cn(str(c)) for c in df.columns}
    return (
        normalize_header_cn("当日盈亏比例") in cols
        and normalize_header_cn("产品类型") in cols
        and normalize_header_cn("总盈亏金额") in cols
    )


def sheet_df_to_alpha_daily_records(
    df: pd.DataFrame, source_file: str, sheet_name: str
) -> list[dict]:
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    rename_map: dict[str, str] = {}
    for c in df.columns:
        nk = normalize_header_cn(c)
        if nk in CANONICAL_HEADER_TO_EN:
            rename_map[c] = CANONICAL_HEADER_TO_EN[nk]
    df = df.rename(columns=rename_map)

    records: list[dict] = []
    for idx, row in df.iterrows():
        item: dict = {
            "_source_file": source_file,
            "_sheet_name": sheet_name,
            "_row_index": int(idx),
            "_schema": ALPHA_DAILY_SCHEMA,
        }
        for en_key, parser in FIELD_PARSERS.items():
            if en_key not in df.columns:
                item[en_key] = None
            else:
                item[en_key] = parser(row[en_key])
        records.append(item)
    return records
