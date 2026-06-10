"""tradelog / position_close_record 证券账户与产品、经纪商对照（strategy_tag = 集合名）。"""
from __future__ import annotations

from typing import TypedDict


class AccountBriefEntry(TypedDict):
    strategy_tag: str
    product: str
    broker: str


# 账户简报左侧列表展示顺序（与业务表格一致，不可打乱）
ACCOUNT_BRIEF_DISPLAY_ORDER: list[AccountBriefEntry] = [
    {"strategy_tag": "DBZQ_18931015", "product": "吾执三零号", "broker": "东北证券"},
    {"strategy_tag": "GTZQ_909800008439", "product": "吾执三零号", "broker": "国投证券"},
    {"strategy_tag": "GTHT_9225553", "product": "量化精选一号", "broker": "国泰海通证券"},
    {"strategy_tag": "ZSZQ_319005550", "product": "量化精选一号", "broker": "浙商证券"},
    {"strategy_tag": "FZZQ_2353038471", "product": "量化精选一号", "broker": "方正证券"},
    {"strategy_tag": "HXZQ_738000000167", "product": "量化精选一号", "broker": "华西证券"},
    {"strategy_tag": "DFZQ_600510888007", "product": "量化精选二号", "broker": "东方证券"},
    {"strategy_tag": "DWZQ_JQ_012000076288", "product": "多元量选", "broker": "东吴证券-沪市"},
    {"strategy_tag": "DWZQ_NF_012000076288", "product": "多元量选", "broker": "东吴证券-深市"},
    {"strategy_tag": "GTZQ_909800008438", "product": "多元量选", "broker": "国投证券"},
    {"strategy_tag": "SWZQ_1673088777", "product": "吾执二二号", "broker": "申万证券"},
    {"strategy_tag": "ZSZQ_1702057978", "product": "吾执二二号", "broker": "浙商证券"},
    {"strategy_tag": "HTZQ_666810103835", "product": "博士一号", "broker": "华泰证券"},
    {"strategy_tag": "DWZQ_JQ_015000094443", "product": "吾执多元一号", "broker": "东吴证券-沪市"},
    {"strategy_tag": "DWZQ_NF_015000094443", "product": "吾执多元一号", "broker": "东吴证券-深市"},
    {"strategy_tag": "GHZQ_17190083", "product": "吾执泽鑫多维", "broker": "国海证券"},
    {"strategy_tag": "ZSZQ_911600210", "product": "吾执一三号", "broker": "浙商证券"},
]

# strategy_tag → 元数据（含未列入展示顺序但可能存在于 tradelog 的集合）
TRADELOG_ACCOUNT_META: dict[str, dict[str, str]] = {
    "DBZQ_18931015": {"product": "吾执三零号", "broker": "东北证券"},
    "GTZQ_909800008439": {"product": "吾执三零号", "broker": "国投证券"},
    "GTHT_9225553": {"product": "量化精选一号", "broker": "国泰海通证券"},
    "ZSZQ_319005550": {"product": "量化精选一号", "broker": "浙商证券"},
    "FZZQ_2353038471": {"product": "量化精选一号", "broker": "方正证券"},
    "HXZQ_738000000167": {"product": "量化精选一号", "broker": "华西证券"},
    "DFZQ_600510888007": {"product": "量化精选二号", "broker": "东方证券"},
    "DWZQ_JQ_012000076288": {"product": "多元量选", "broker": "东吴证券-沪市"},
    "DWZQ_NF_012000076288": {"product": "多元量选", "broker": "东吴证券-深市"},
    "GTZQ_909800008438": {"product": "多元量选", "broker": "国投证券"},
    "SWZQ_1673088777": {"product": "吾执二二号", "broker": "申万证券"},
    "ZSZQ_1702057978": {"product": "吾执二二号", "broker": "浙商证券"},
    "HTZQ_666810103835": {"product": "博士一号", "broker": "华泰证券"},
    "DWZQ_JQ_015000094443": {"product": "吾执多元一号", "broker": "东吴证券-沪市"},
    "DWZQ_NF_015000094443": {"product": "吾执多元一号", "broker": "东吴证券-深市"},
    "GHZQ_17190083": {"product": "吾执泽鑫多维", "broker": "国海证券"},
    "ZSZQ_911600210": {"product": "吾执一三号", "broker": "浙商证券"},
}


# 表名 → 资金账号（默认取最后一段；下列为业务指定）
_FUND_ACCOUNT_OVERRIDES: dict[str, str] = {
    "DWZQ_JQ_012000076288": "12000076288",
    "DWZQ_NF_012000076288": "12000076288",
    "DWZQ_JQ_015000094443": "15000094443",
    "DWZQ_NF_015000094443": "15000094443",
}


def fund_account_from_strategy_tag(strategy_tag: str) -> str:
    """从 tradelog / position_close_record 集合名（strategy_tag）解析资金账号。"""
    tag = (strategy_tag or "").strip()
    if not tag:
        return ""
    if tag in _FUND_ACCOUNT_OVERRIDES:
        return _FUND_ACCOUNT_OVERRIDES[tag]
    parts = tag.split("_")
    return parts[-1] if len(parts) >= 2 else tag


def account_meta_for_strategy_tag(strategy_tag: str) -> dict[str, str]:
    tag = (strategy_tag or "").strip()
    meta = TRADELOG_ACCOUNT_META.get(tag)
    if meta:
        return dict(meta)
    return {"product": tag or "—", "broker": "—"}
