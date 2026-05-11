"""真实净值 fund_nav_real 门户列表查询（博士一号 WZ_BSYH_* + 泽鑫多维 WZ_ZXDW_*）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from django.conf import settings

from portal.data.fund_nav_real_config import (
    FUND_NAV_PRODUCTS,
    FundNavProduct,
    fund_nav_portal_sidebar_allowed_keys,
)
from portal.db.mongo import get_fund_nav_collection, get_fund_nav_zxdw_nav_collection


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
    zxdw = frozenset(getattr(settings, "MONGODB_ZXDW_NAV_COLLECTIONS", ()))
    bsyh = frozenset(
        {
            settings.NAV_REAL_WZ_BSYH_MASTER,
            settings.NAV_REAL_WZ_BSYH_B,
            getattr(settings, "NAV_REAL_WZ_EEH_MASTER", "WZ_EEH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_DYYH_MASTER", "WZ_DYYH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_SLH_MASTER", "WZ_SLH_MASTER"),
            getattr(settings, "NAV_REAL_WZ_DYLX_MASTER", "WZ_DYLX_MASTER"),
            getattr(settings, "NAV_REAL_WZ_YSH_MASTER", "WZ_YSH_MASTER"),
        }
    )
    return bsyh | zxdw


def normalize_fund_nav_product_key_params(raw_keys: list[str]) -> list[str]:
    """只保留允许的 NAV_REAL_* 与 ZXDW 集合名，去重保序。"""
    allowed = _allowed_product_keys()
    out: list[str] = []
    for k in raw_keys:
        s = (k or "").strip()
        if s in allowed and s not in out:
            out.append(s)
    return out


def _zxdw_product_keys() -> frozenset[str]:
    return frozenset(getattr(settings, "MONGODB_ZXDW_NAV_COLLECTIONS", ()))


def _normalize_zxdw_nav_doc(doc: dict[str, Any], fund: FundNavProduct) -> dict[str, Any]:
    """五列净值表字段 -> 门户表格字段（与博士一号列一致）。"""
    row = dict(doc)
    row.pop("_id", None)
    row["asset_code"] = str(row.get("asset_code") or "").strip()
    row["asset_name"] = str(row.get("product_name") or "").strip()
    if row.get("cumulative_unit_nav") is None and row.get("cumulative_nav") is not None:
        row["cumulative_unit_nav"] = row.get("cumulative_nav")
    row["product_key"] = fund["product_key"]
    row["product_label"] = fund["name_prefix"]
    return row


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
    form_submitted 为 False（首次进入页面）：未传 nav_q 时返回 None，表示查询全部已配置产品。
    form_submitted 为 True：若未勾选任何产品则返回 []；否则返回规范化后的编码列表。
    """
    raw = [x for x in get.getlist("product_key") if (x or "").strip()]
    if not form_submitted:
        return None
    if not raw:
        return []
    return normalize_fund_nav_product_key_params(raw)


def fund_nav_product_keys_from_raw_nav_request(
    get, *, form_submitted: bool
) -> list[str] | None:
    """基金净值页：侧栏单选 product_key，仅允许主份额（不含 A/B/C 类）。"""
    if not form_submitted:
        return None
    raw = (get.get("product_key") or "").strip()
    if not raw:
        return []
    allowed = fund_nav_portal_sidebar_allowed_keys()
    if raw not in allowed:
        return []
    return [raw]


def fetch_fund_nav_portal_documents(
    *,
    limit: int = 200,
    date_from: str | None = None,
    date_to: str | None = None,
    product_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    从博士一号集合（WZ_BSYH_*）与泽鑫多维集合（WZ_ZXDW_*）读取净值行，合并后按净值日倒序截断 limit。
    """
    funds = _funds_for_filter(product_keys)
    if not funds:
        return []
    base_q = build_fund_nav_mongo_query(date_from, date_to)
    cap = max(1, min(limit, 10000))
    has_nav_date_filter = _nav_date_range_clause(date_from, date_to) is not None
    zxdw_keys = _zxdw_product_keys()

    merged: list[dict[str, Any]] = []
    for fund in funds:
        pk = fund["product_key"]
        if pk in zxdw_keys:
            coll = get_fund_nav_zxdw_nav_collection(pk)
            cursor = coll.find(base_q).sort([("nav_date", -1), ("asset_code", 1)])
        else:
            coll = get_fund_nav_collection(pk)
            cursor = coll.find(base_q).sort([("nav_date", -1), ("asset_code", 1)])
        # 无净值日条件时避免全表扫描（数据量极大时仍建议在页面选择净值日区间）
        if not has_nav_date_filter:
            cursor = cursor.limit(min(5000, max(500, cap * 3)))
        for doc in cursor:
            if pk in zxdw_keys:
                merged.append(_normalize_zxdw_nav_doc(doc, fund))
            else:
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
