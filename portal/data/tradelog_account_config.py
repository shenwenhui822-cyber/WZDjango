"""tradelog / position_close_record 证券账户与产品、经纪商对照（strategy_tag = 集合名）。

部分条目为期货实时库（source=rt_future），不走 tradelog / position_close_record。
"""
from __future__ import annotations

from typing import TypedDict

try:
    from typing import NotRequired
except ImportError:  # Python < 3.11
    from typing_extensions import NotRequired


class AccountBriefEntry(TypedDict):
    strategy_tag: str
    product: str
    broker: str
    # 默认 position_close；rt_future 表示读期货实时库最新一条
    source: NotRequired[str]
    mongo_db: NotRequired[str]
    mongo_collection: NotRequired[str]


SOURCE_POSITION_CLOSE = "position_close"
SOURCE_RT_FUTURE = "rt_future"


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
    {
        "strategy_tag": "GMQH_59000028",
        "product": "吾执二二号",
        "broker": "国贸期货",
        "source": SOURCE_RT_FUTURE,
        "mongo_db": "rt_future",
        "mongo_collection": "GMQH_59000028",
    },
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
    "GMQH_59000028": {"product": "吾执二二号", "broker": "国贸期货"},
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
    "GMQH_59000028": "59000028",
}


def entry_for_strategy_tag(strategy_tag: str) -> AccountBriefEntry | None:
    tag = (strategy_tag or "").strip()
    for entry in ACCOUNT_BRIEF_DISPLAY_ORDER:
        if entry["strategy_tag"] == tag:
            return entry
    return None


def account_source(strategy_tag: str) -> str:
    entry = entry_for_strategy_tag(strategy_tag)
    if not entry:
        return SOURCE_POSITION_CLOSE
    return (entry.get("source") or SOURCE_POSITION_CLOSE).strip() or SOURCE_POSITION_CLOSE


def is_rt_future_account(strategy_tag: str) -> bool:
    return account_source(strategy_tag) == SOURCE_RT_FUTURE


def rt_future_locator(strategy_tag: str) -> tuple[str, str] | None:
    """返回 (db, collection)；非 rt_future 账户返回 None。"""
    entry = entry_for_strategy_tag(strategy_tag)
    if not entry or (entry.get("source") or SOURCE_POSITION_CLOSE) != SOURCE_RT_FUTURE:
        return None
    db = (entry.get("mongo_db") or "rt_future").strip()
    coll = (entry.get("mongo_collection") or entry["strategy_tag"]).strip()
    return db, coll


def stock_strategy_tags() -> list[str]:
    """仅证券账户（用于 tradelog → position_close_record 同步）。"""
    return [
        e["strategy_tag"]
        for e in ACCOUNT_BRIEF_DISPLAY_ORDER
        if (e.get("source") or SOURCE_POSITION_CLOSE) != SOURCE_RT_FUTURE
    ]


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
