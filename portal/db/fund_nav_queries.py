"""博士一号真实净值（fund_nav_real）门户列表查询。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from django.conf import settings

from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, FundNavProduct
from portal.db.mongo import get_fund_nav_collection


def _parse_iso_day(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y-%m-%d")


def _nav_date_range_clause(
    date_from: str | None, date_to: str | None
) -> dict[str, Any] | None:
    """净值日范围（含端点），兼容 nav_date 为 YYYY-MM-DD 字符串或 BSON datetime。"""
    df = (date_from or "").strip() or None
    dt = (date_to or "").strip() or None
    if not df and not dt:
        return None
    if df:
        _parse_iso_day(df)
    if dt:
        _parse_iso_day(dt)
    if df and dt and df > dt:
        df, dt = dt, df

    branches: list[dict[str, Any]] = []

    scond: dict[str, Any] = {}
    if df:
        scond["$gte"] = df
    if dt:
        scond["$lte"] = dt
    if scond:
        branches.append({"nav_date": scond})

    dcond: dict[str, Any] = {}
    if df:
        dcond["$gte"] = _parse_iso_day(df)
    if dt:
        end = _parse_iso_day(dt)
        end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
        dcond["$lte"] = end
    if dcond:
        branches.append({"nav_date": dcond})

    if not branches:
        return None
    if len(branches) == 1:
        return branches[0]
    return {"$or": branches}


def build_fund_nav_mongo_query(
    date_from: str | None, date_to: str | None
) -> dict[str, Any]:
    """单集合查询条件（用于调试展示）；与 fetch 内各集合 filter 一致。"""
    q: dict[str, Any] = {}
    clause = _nav_date_range_clause(date_from, date_to)
    if clause is not None:
        if "$or" in clause:
            q["$or"] = clause["$or"]
        else:
            q.update(clause)
    return q


def _allowed_product_keys() -> frozenset[str]:
    return frozenset(
        {
            settings.NAV_REAL_WZ_BSYH_MASTER,
            settings.NAV_REAL_WZ_BSYH_B,
        }
    )


def normalize_fund_nav_product_key_params(raw_keys: list[str]) -> list[str]:
    """只保留允许的 NAV_REAL_* 逻辑编码，去重保序。"""
    allowed = _allowed_product_keys()
    out: list[str] = []
    for k in raw_keys:
        s = (k or "").strip()
        if s in allowed and s not in out:
            out.append(s)
    return out


def _funds_for_filter(product_keys: list[str] | None) -> list[FundNavProduct]:
    """product_keys 为 None 表示未筛选（全部产品）；空列表表示无匹配产品，不读库。"""
    if product_keys is None:
        return list(FUND_NAV_PRODUCTS)
    if not product_keys:
        return []
    keyset = set(product_keys)
    return [f for f in FUND_NAV_PRODUCTS if f["product_key"] in keyset]


def fund_nav_product_keys_from_request(
    get, *, form_submitted: bool
) -> list[str] | None:
    """
    解析 GET 中的 product_key 多选。
    form_submitted 为 False（首次进入页面）：未传 nav_q 时返回 None，表示两个产品都查。
    form_submitted 为 True：若未勾选任何产品则返回 []；否则返回规范化后的编码列表。
    """
    raw = [x for x in get.getlist("product_key") if (x or "").strip()]
    if not form_submitted:
        return None
    if not raw:
        return []
    return normalize_fund_nav_product_key_params(raw)


def fetch_fund_nav_portal_documents(
    *,
    limit: int = 200,
    date_from: str | None = None,
    date_to: str | None = None,
    product_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    从 WZ_BSYH_MASTER / WZ_BSYH_B 集合读取净值行，合并后按净值日倒序截断 limit。
    """
    funds = _funds_for_filter(product_keys)
    if not funds:
        return []
    base_q = build_fund_nav_mongo_query(date_from, date_to)
    cap = max(1, min(limit, 10000))
    has_nav_date_filter = _nav_date_range_clause(date_from, date_to) is not None

    merged: list[dict[str, Any]] = []
    for fund in funds:
        coll = get_fund_nav_collection(fund["product_key"])
        cursor = coll.find(base_q).sort([("nav_date", -1), ("asset_code", 1)])
        # 无净值日条件时避免全表扫描（数据量极大时仍建议在页面选择净值日区间）
        if not has_nav_date_filter:
            cursor = cursor.limit(min(5000, max(500, cap * 3)))
        for doc in cursor:
            doc = dict(doc)
            doc.pop("_id", None)
            doc["product_key"] = fund["product_key"]
            doc["product_label"] = fund["name_prefix"]
            merged.append(doc)

    def sort_key(d: dict[str, Any]) -> tuple[str, str]:
        nd = d.get("nav_date")
        if hasattr(nd, "strftime"):
            day = nd.strftime("%Y-%m-%d")
        else:
            day = str(nd or "")[:10]
        return day, str(d.get("product_key") or "")

    merged.sort(key=sort_key, reverse=True)
    return merged[:cap]
