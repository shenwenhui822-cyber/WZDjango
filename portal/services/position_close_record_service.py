"""tradelog → position_close_record 收盘快照同步与账户简报查询。"""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import time as dt_time
from typing import Any

from django.utils import timezone

from portal.services.trade_calendar_service import (
    is_trade_date_iso,
    prev_trading_day_iso_before,
    trading_date_iso_set,
)

from portal.data.tradelog_account_config import (
    ACCOUNT_BRIEF_DISPLAY_ORDER,
    account_meta_for_strategy_tag,
    fund_account_from_strategy_tag,
    is_rt_future_account,
    rt_future_locator,
    stock_strategy_tags,
)
from portal.db.mongo import (
    get_mongo_client,
    get_position_close_record_collection,
    get_tradelog_collection,
    list_position_close_record_collection_names,
)

_STRATEGY_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
# 收盘快照数据窗口：取当日该时刻（含）之后最后一次 tradelog 落库（定时任务 15:45 触发）
_TRADELOG_CLOSE_SNAPSHOT_AFTER = dt_time(15, 29)


def configured_strategy_tags() -> list[str]:
    """证券账户 strategy_tag（同步默认范围，不含 rt_future 期货账户）。"""
    return stock_strategy_tags()


def _tradelog_t_iso_range_for_close_snapshot(trade_date: str) -> tuple[str, str]:
    """返回 t_iso 半开区间 [day 15:29:00, next_day 00:00:00)。"""
    from datetime import date, timedelta

    day = (trade_date or "").strip()[:10]
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    lower = f"{day} {_TRADELOG_CLOSE_SNAPSHOT_AFTER.strftime('%H:%M:%S')}"
    return lower, next_day


def _tradelog_t_unix_range_for_close_snapshot(trade_date: str) -> tuple[float, float]:
    """与 t_iso 区间等价：本地时区 trade_date 15:29（含）至次日 0 点（不含）。"""
    from datetime import date, datetime, timedelta

    day = (trade_date or "").strip()[:10]
    d = date.fromisoformat(day)
    tzinfo = timezone.get_current_timezone()
    start = timezone.make_aware(
        datetime.combine(d, _TRADELOG_CLOSE_SNAPSHOT_AFTER),
        tzinfo,
    )
    end = timezone.make_aware(
        datetime.combine(d + timedelta(days=1), dt_time(0, 0, 0)),
        tzinfo,
    )
    return start.timestamp(), end.timestamp()


def _close_snapshot_query(trade_date: str) -> dict[str, Any]:
    """t_iso 字符串区间 + t_unix 数值区间（兼容缺 t_iso 或格式异常）。"""
    t_lower, t_upper = _tradelog_t_iso_range_for_close_snapshot(trade_date)
    u0, u1 = _tradelog_t_unix_range_for_close_snapshot(trade_date)
    day = (trade_date or "").strip()[:10]
    return {
        "$or": [
            {"t_iso": {"$gte": t_lower, "$lt": t_upper}},
            {
                "t_unix": {"$gte": u0, "$lt": u1},
                "t_iso": {"$regex": f"^{re.escape(day)} "},
            },
            {"t_unix": {"$gte": u0, "$lt": u1}},
        ]
    }


def _latest_tradelog_t_iso_samples(strategy_tag: str, *, limit: int = 2) -> list[str]:
    try:
        coll = get_tradelog_collection(strategy_tag)
    except ValueError:
        return []
    out: list[str] = []
    for doc in coll.find({}, {"t_iso": 1, "_id": 0}).sort("t_unix", -1).limit(limit):
        t = doc.get("t_iso")
        if t is not None:
            out.append(str(t))
    return out


def _validate_strategy_tag(name: str) -> str:
    tag = (name or "").strip()
    if not _STRATEGY_TAG_RE.fullmatch(tag):
        raise ValueError(f"非法 tradelog 集合名: {name!r}")
    return tag


def _strip_mongo_id(doc: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(doc)
    oid = out.pop("_id", None)
    if oid is not None:
        out["source_tradelog_id"] = str(oid)
    return out


def fetch_latest_tradelog_doc(strategy_tag: str, trade_date: str) -> dict[str, Any] | None:
    """
    取 tradelog 指定集合在 trade_date 当天 15:29（含）之后 t_unix 最新一条。
    """
    tag = _validate_strategy_tag(strategy_tag)
    day = (trade_date or "").strip()[:10]
    coll = get_tradelog_collection(tag)
    return coll.find_one(_close_snapshot_query(day), sort=[("t_unix", -1)])


def sync_position_close_for_date(
    trade_date: str,
    *,
    strategy_tags: list[str] | None = None,
) -> dict[str, Any]:
    """
    将 tradelog 各表当日 15:29 后最后一次落库写入 position_close_record（同名集合）。
    默认仅同步 ACCOUNT_BRIEF_DISPLAY_ORDER 中配置的账户。
    按 snapshot_date upsert，同一集合同一业务日仅保留一条记录（重复执行覆盖）。
    """
    day = (trade_date or "").strip()[:10]
    if len(day) != 10:
        raise ValueError(f"trade_date 须为 YYYY-MM-DD，当前: {trade_date!r}")

    tags = strategy_tags or configured_strategy_tags()
    synced_at = timezone.now()
    details: list[dict[str, Any]] = []
    ok_count = miss_count = err_count = 0

    for tag in tags:
        try:
            _validate_strategy_tag(tag)
        except ValueError as exc:
            err_count += 1
            details.append({"strategy_tag": tag, "status": "error", "message": str(exc)})
            continue
        try:
            src = fetch_latest_tradelog_doc(tag, day)
            if not src:
                miss_count += 1
                samples = _latest_tradelog_t_iso_samples(tag)
                hint = f"{day} 15:29 后无 tradelog 记录"
                if samples:
                    hint += f"（库内最新 t_iso: {', '.join(samples)}）"
                else:
                    hint += "（集合为空）"
                details.append(
                    {
                        "strategy_tag": tag,
                        "status": "missing",
                        "message": hint,
                    }
                )
                continue
            payload = _strip_mongo_id(src)
            payload["snapshot_date"] = day
            payload["synced_at"] = synced_at
            dest = get_position_close_record_collection(tag)
            dest.update_one(
                {"snapshot_date": day},
                {"$set": payload},
                upsert=True,
            )
            ok_count += 1
            t_iso = src.get("t_iso") or ""
            details.append(
                {
                    "strategy_tag": tag,
                    "status": "ok",
                    "t_iso": t_iso,
                    "message": "已写入",
                }
            )
        except Exception as exc:
            err_count += 1
            details.append(
                {
                    "strategy_tag": tag,
                    "status": "error",
                    "message": str(exc),
                }
            )

    return {
        "trade_date": day,
        "total_collections": len(tags),
        "synced": ok_count,
        "missing": miss_count,
        "errors": err_count,
        "details": details,
    }


def list_position_close_strategy_tags() -> list[str]:
    return list_position_close_record_collection_names()


def latest_snapshot_date_for_tag(strategy_tag: str) -> str | None:
    tag = _validate_strategy_tag(strategy_tag)
    coll = get_position_close_record_collection(tag)
    doc = coll.find_one({}, sort=[("snapshot_date", -1)], projection={"snapshot_date": 1})
    if not doc:
        return None
    return str(doc.get("snapshot_date") or "")[:10] or None


def load_snapshot_doc(strategy_tag: str, snapshot_date: str | None = None) -> dict[str, Any] | None:
    tag = _validate_strategy_tag(strategy_tag)
    if is_rt_future_account(tag):
        return None
    coll = get_position_close_record_collection(tag)
    if snapshot_date:
        day = snapshot_date.strip()[:10]
        return coll.find_one({"snapshot_date": day})
    return coll.find_one(sort=[("snapshot_date", -1)])


def list_rt_future_snapshot_dates(strategy_tag: str) -> list[str]:
    """返回 rt_future 集合中已有 snapshot_date（升序 YYYY-MM-DD）。"""
    loc = rt_future_locator(strategy_tag)
    if not loc:
        return []
    db_name, coll_name = loc
    coll = get_mongo_client()[db_name][coll_name]
    raw = coll.distinct("snapshot_date")
    return sorted({str(d).strip()[:10] for d in raw if d})


def load_rt_future_doc(
    strategy_tag: str,
    snapshot_date: str | None = None,
) -> dict[str, Any] | None:
    """读取 rt_future 文档：指定 snapshot_date 取当日最新一条，否则取全集最新。"""
    loc = rt_future_locator(strategy_tag)
    if not loc:
        return None
    db_name, coll_name = loc
    coll = get_mongo_client()[db_name][coll_name]
    sort_key = [("timestamp", -1), ("_id", -1)]
    day = (snapshot_date or "").strip()[:10]
    if day:
        return coll.find_one({"snapshot_date": day}, sort=sort_key)
    return coll.find_one({}, sort=sort_key)


def load_rt_future_latest_doc(strategy_tag: str) -> dict[str, Any] | None:
    """读取 rt_future 集合最新一条（按 timestamp、_id 降序）。"""
    return load_rt_future_doc(strategy_tag)


def resolve_rt_future_snapshot_date(
    strategy_tag: str,
    explicit: str | None,
) -> tuple[str, list[str]]:
    """
    期货账户交易日：仅允许集合内存在的 snapshot_date。
    返回 (effective_date, available_dates升序)。
    未选或所选日不在集合中时，落到最新 snapshot_date。
    """
    dates = list_rt_future_snapshot_dates(strategy_tag)
    day = (explicit or "").strip()[:10]
    if day and day in set(dates):
        return day, dates
    if dates:
        return dates[-1], dates
    return day, dates


_RT_FUTURE_BRIEF_FIELD_SPEC: list[tuple[str, str]] = [
    ("资金账号", "account_id"),
    ("账户类型", "account_type"),
    ("快照日期", "snapshot_date"),
    ("快照时间", "timestamp"),
    ("保证金占用", "margin_used"),
    ("可用资金", "available_funds"),
    ("资金使用率", "funds_utilization_rate"),
    ("风险度", "risk_level"),
    ("持仓合约数", "position_count"),
]


def build_rt_future_brief_rows(
    doc: dict[str, Any] | None,
    *,
    strategy_tag: str = "",
) -> list[dict[str, str]]:
    positions = (doc or {}).get("positions") or []

    def val_for_field(field: str) -> str:
        if not doc:
            return "—"
        if field == "account_id":
            return fund_account_from_strategy_tag(strategy_tag) or "—"
        if field == "account_type":
            return "期货"
        if field == "snapshot_date":
            return str(doc.get("snapshot_date") or "—")[:10] or "—"
        if field == "timestamp":
            return str(doc.get("timestamp") or "—")
        if field == "margin_used":
            return _fmt_num(doc.get("margin_used"))
        if field == "available_funds":
            return _fmt_num(doc.get("available_funds"))
        if field == "funds_utilization_rate":
            return _fmt_pct(doc.get("funds_utilization_rate"))
        if field == "risk_level":
            return _fmt_pct(doc.get("risk_level"))
        if field == "position_count":
            return str(len(positions))
        return "—"

    return [
        {"label": label, "field": field, "value": val_for_field(field)}
        for label, field in _RT_FUTURE_BRIEF_FIELD_SPEC
    ]


def build_rt_future_position_rows(doc: dict[str, Any] | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for p in (doc or {}).get("positions") or []:
        rows.append(
            {
                "contract": str(p.get("contract") or "—"),
                "direction": str(p.get("direction") or "—"),
                "total_position": str(int(p.get("total_position") or 0)),
                "average_opening_price": _fmt_num(p.get("average_opening_price")),
            }
        )
    return rows


def _fmt_num(v: Any, *, digits: int = 2) -> str:
    if v is None or v == "":
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{n:,.{digits}f}"


def _fmt_pct(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{n:.4f}%"


# QMT 未赋值浮点常返回 DBL_MAX（约 1.797e308），视为无效
_QMT_INVALID_ABS = 1e308


def _qmt_raw_num(src: dict[str, Any] | None, key: str) -> Any:
    """从 QMT 原始 dict 读数值；缺失或哨兵值返回 None。"""
    if not src or key not in src:
        return None
    raw = src.get(key)
    if raw is None or raw == "":
        return None
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n) or abs(n) >= _QMT_INVALID_ABS:
        return None
    return n


def _credit_raw_sources(doc: dict[str, Any] | None, acct: dict[str, Any]) -> list[dict[str, Any]]:
    """两融/账户原始行查找顺序：query_meta 信用行 → 普通账户行 → accounts.row_debug。"""
    sources: list[dict[str, Any]] = []
    qm = (doc or {}).get("query_meta") or []
    if isinstance(qm, list) and qm and isinstance(qm[0], dict):
        for key in ("raw_credit_account_0", "raw_account_0"):
            block = qm[0].get(key)
            if isinstance(block, dict):
                sources.append(block)
    debug = acct.get("row_debug")
    if isinstance(debug, dict):
        sources.append(debug)
    return sources


def _credit_field_num(doc: dict[str, Any] | None, acct: dict[str, Any], *keys: str) -> Any:
    """按 sources × keys 优先级取第一个有效数值。"""
    for src in _credit_raw_sources(doc, acct):
        for key in keys:
            v = _qmt_raw_num(src, key)
            if v is not None:
                return v
    return None


# 账户简报右侧字段顺序（与业务说明 §4 一致，不可调整）
_ACCOUNT_BRIEF_FIELD_SPEC: list[tuple[str, str]] = [
    ("资金账号", "account_id"),
    ("账户类型", "account_type"),
    ("总资产", "total_asset"),
    ("净资产", "net_assets"),
    ("总负债", "total_liabilities"),
    ("持仓市值", "market_value"),
    ("可用资金", "available_cash"),
    ("持仓浮动盈亏", "position_profit"),
    ("持仓只数", "position_summary.count"),
    ("持仓总盈亏", "position_summary.total_profit"),
    ("当日平均持仓涨跌幅", "position_summary.avg_change_pct"),
    ("上涨 / 下跌 / 平盘", "up_count / down_count / flat_count"),
]


_ACCOUNT_BRIEF_CUTOFF = dt_time(15, 45)


def default_account_brief_snapshot_date(*, now=None) -> str:
    """
    默认展示日：
    - 交易日 15:45 前 → 上一交易日
    - 交易日 15:45 后 → 当日
    - 非交易日 → 最近一个交易日（严格早于今日的交易日）
    """
    local_now = now or timezone.localtime()
    run_iso = local_now.date().isoformat()
    if not is_trade_date_iso(run_iso):
        return prev_trading_day_iso_before(run_iso) or run_iso
    cutoff = local_now.replace(
        hour=_ACCOUNT_BRIEF_CUTOFF.hour,
        minute=_ACCOUNT_BRIEF_CUTOFF.minute,
        second=0,
        microsecond=0,
    )
    if local_now < cutoff:
        return prev_trading_day_iso_before(run_iso) or run_iso
    return run_iso


def resolve_account_brief_snapshot_date(explicit: str | None) -> tuple[str, bool]:
    """返回 (snapshot_date, user_picked)。"""
    day = (explicit or "").strip()[:10]
    if day:
        return day, True
    return default_account_brief_snapshot_date(), False


def trade_dates_for_calendar_json() -> str:
    """flatpickr enable 用：已排序的交易日 YYYY-MM-DD 列表 JSON。"""
    return json.dumps(sorted(trading_date_iso_set()), ensure_ascii=False)


def build_account_brief_rows(
    doc: dict[str, Any] | None,
    *,
    strategy_tag: str = "",
) -> list[dict[str, str]]:
    """按固定顺序输出：项目、数值（field 仅保留供模板判断对齐）。"""
    accounts = (doc or {}).get("accounts") or []
    acct = accounts[0] if accounts else {}
    summary = acct.get("position_summary") or {}
    binding = (doc or {}).get("binding") or {}
    acct_type = acct.get("account_type") or binding.get("binding_acct_type") or "—"

    def val_for_field(field: str) -> str:
        if not doc:
            return "—"
        if field == "account_id":
            acct_id = fund_account_from_strategy_tag(strategy_tag)
            return acct_id or "—"
        if field == "account_type":
            return str(acct_type)
        if field == "total_asset":
            return _fmt_num(acct.get("total_asset"))
        if field == "net_assets":
            # query_meta.raw_credit_account_0.m_dAssureAsset（净资产）
            return _fmt_num(_credit_field_num(doc, acct, "m_dAssureAsset"))
        if field == "total_liabilities":
            # m_dTotalDebt（查柜台）→ m_dTotalDebit → m_dFinDebt
            return _fmt_num(
                _credit_field_num(
                    doc, acct, "m_dTotalDebt", "m_dTotalDebit", "m_dFinDebt"
                )
            )
        if field == "market_value":
            return _fmt_num(acct.get("market_value"))
        if field == "available_cash":
            return _fmt_num(acct.get("available_cash"))
        if field == "position_profit":
            return _fmt_num(acct.get("position_profit"))
        if field == "position_summary.count":
            return str(summary.get("count", "—"))
        if field == "position_summary.total_profit":
            return _fmt_num(summary.get("total_profit"))
        if field == "position_summary.avg_change_pct":
            return _fmt_pct(summary.get("avg_change_pct"))
        if field == "up_count / down_count / flat_count":
            up = summary.get("up_count", 0)
            down = summary.get("down_count", 0)
            flat = summary.get("flat_count", 0)
            return f"{up} / {down} / {flat}"
        return "—"

    return [
        {"label": label, "field": field, "value": val_for_field(field)}
        for label, field in _ACCOUNT_BRIEF_FIELD_SPEC
    ]


def _build_sidebar_items(snapshot_date: str) -> list[dict[str, Any]]:
    """左侧导航：产品名字 - 证券经纪商（顺序固定）。"""
    items: list[dict[str, Any]] = []
    for entry in ACCOUNT_BRIEF_DISPLAY_ORDER:
        tag = entry["strategy_tag"]
        product = entry["product"]
        meta = account_meta_for_strategy_tag(tag)
        broker = entry.get("broker") or meta["broker"]
        if is_rt_future_account(tag):
            has_data = load_rt_future_doc(tag, snapshot_date) is not None
        else:
            has_data = load_snapshot_doc(tag, snapshot_date) is not None
        items.append(
            {
                "strategy_tag": tag,
                "product": product,
                "broker": broker,
                "label": f"{product} - {broker}",
                "has_data": has_data,
                "source": entry.get("source") or "position_close",
            }
        )
    return items


def build_account_brief_page_context(
    *,
    selected_tag: str | None = None,
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    allowed_tags = [e["strategy_tag"] for e in ACCOUNT_BRIEF_DISPLAY_ORDER]
    effective_date, user_picked_date = resolve_account_brief_snapshot_date(snapshot_date)
    sidebar_items = _build_sidebar_items(effective_date)

    sel = (selected_tag or "").strip()
    if not sel and allowed_tags:
        sel = allowed_tags[0]
    if sel not in allowed_tags:
        sel = allowed_tags[0] if allowed_tags else ""

    meta = account_meta_for_strategy_tag(sel) if sel else {"product": "", "broker": ""}
    is_future = bool(sel) and is_rt_future_account(sel)

    if is_future:
        # 交易日仅展示该集合已有 snapshot_date；按所选日取当日最新一条
        fut_date, fut_dates = resolve_rt_future_snapshot_date(
            sel,
            effective_date if user_picked_date else None,
        )
        fut_doc = load_rt_future_doc(sel, fut_date) if sel and fut_date else None
        sidebar_items = _build_sidebar_items(fut_date)
        dates_json = json.dumps(fut_dates, ensure_ascii=False)
        return {
            "brief_sidebar_items": sidebar_items,
            "selected_strategy_tag": sel,
            "selected_product": meta.get("product", ""),
            "selected_broker": meta.get("broker", ""),
            "snapshot_date": fut_date,
            "snapshot_date_user_picked": user_picked_date and bool(fut_date),
            "trade_dates": fut_dates,
            "trade_dates_json": dates_json,
            "brief_rows": build_rt_future_brief_rows(fut_doc, strategy_tag=sel),
            "brief_future_positions": build_rt_future_position_rows(fut_doc),
            "brief_is_rt_future": True,
            "brief_has_data": bool(fut_doc),
            "brief_t_iso": str((fut_doc or {}).get("timestamp") or ""),
        }

    doc = load_snapshot_doc(sel, effective_date) if sel else None
    stock_dates = sorted(trading_date_iso_set())
    return {
        "brief_sidebar_items": sidebar_items,
        "selected_strategy_tag": sel,
        "selected_product": meta.get("product", ""),
        "selected_broker": meta.get("broker", ""),
        "snapshot_date": effective_date,
        "snapshot_date_user_picked": user_picked_date,
        "trade_dates": stock_dates,
        "trade_dates_json": json.dumps(stock_dates, ensure_ascii=False),
        "brief_rows": build_account_brief_rows(doc, strategy_tag=sel),
        "brief_future_positions": [],
        "brief_is_rt_future": False,
        "brief_has_data": bool(doc),
        "brief_t_iso": str(doc.get("t_iso") or "") if doc else "",
    }
