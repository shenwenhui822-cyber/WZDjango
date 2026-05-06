"""占位页面视图（待补充业务内容）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from portal.db.mongo import get_mongo_client

FUTURE_POINT_MULTIPLIER: dict[str, int] = {
    "IF": 300,
    "IH": 300,
    "IC": 200,
    "IM": 200,
}


def _future_direction_sign(direction: str | None) -> int:
    """期货方向：buy 计为空头为负、sell 为正（与空头市值口径一致）。"""
    d = (direction or "").strip().lower()
    if d in ("buy", "long", "b", "买", "多"):
        return -1
    if d in ("sell", "short", "s", "卖", "空"):
        return 1
    return 1


def _fmt_num(v: Any, digits: int = 2) -> str:
    try:
        if v is None:
            return "-"
        n = float(v)
        return f"{n:,.{digits}f}"
    except Exception:
        return str(v) if v is not None else "-"


def _fmt_pct(v: Any, digits: int = 2) -> str:
    try:
        if v is None:
            return "-"
        return f"{float(v):.{digits}f}%"
    except Exception:
        return "-"


def _normalize_future_collections(future_collections: str | Sequence[str]) -> list[str]:
    if isinstance(future_collections, str):
        return [future_collections]
    return list(future_collections)


def _build_market_neutral_pair(
    future_collections: str | Sequence[str],
    stock_collection: str,
    client: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    cols = _normalize_future_collections(future_collections)

    stock_doc = (
        client["rt_stock"][stock_collection]
        .find({}, {"_id": 0})
        .sort([("ts", -1), ("date", -1), ("time", -1), ("_id", -1)])
        .limit(1)
    )
    stock_latest = next(iter(stock_doc), {})

    stock_market_value = (
        stock_latest.get("stock_market_value")
        or stock_latest.get("market_value")
        or stock_latest.get("total_market_value")
    )
    stock_available_cash = stock_latest.get("available_cash")
    stock_ts = (
        f"{stock_latest.get('date') or '-'} {stock_latest.get('time') or '-'}".strip()
        if stock_latest
        else "-"
    )
    stock_remark = (
        f"资产总额 {_fmt_num(stock_latest.get('total_asset'))}\n"
        f"股票市值 {_fmt_num(stock_market_value)}"
        if stock_latest
        else "-"
    )

    future_market_value_total = 0.0
    future_rows: list[dict[str, Any]] = []
    timestamps: list[str] = []

    for coll in cols:
        future_doc = (
            client["rt_future"][coll]
            .find({}, {"_id": 0})
            .sort([("timestamp", -1), ("_id", -1)])
            .limit(1)
        )
        future_latest = next(iter(future_doc), {})

        positions = future_latest.get("positions") or []
        formula_terms: list[str] = []
        future_notional_value = 0.0
        for p in positions[:10]:
            contract = p.get("contract") or "-"
            direction = p.get("direction") or "-"
            total_position = int(p.get("total_position") or 0)
            lots = abs(total_position)
            avg_px_raw = p.get("average_opening_price") or 0
            contract_upper = str(contract).upper()
            prefix = "".join(ch for ch in contract_upper if ch.isalpha())[:2]
            multi = FUTURE_POINT_MULTIPLIER.get(prefix)
            if multi and avg_px_raw and lots:
                sign = _future_direction_sign(direction)
                px = abs(float(avg_px_raw))
                term_value = sign * px * lots * multi
                future_notional_value += term_value
                if sign < 0:
                    formula_terms.append(
                        f"(-1)*{contract}({direction}) {lots}*{_fmt_num(avg_px_raw, 2)}*{multi}"
                    )
                else:
                    formula_terms.append(
                        f"{contract}({direction}) {lots}*{_fmt_num(avg_px_raw, 2)}*{multi}"
                    )

        if formula_terms:
            contrib = float(future_notional_value)
        else:
            contrib = float(future_latest.get("margin_used") or 0)
        future_market_value_total += contrib
        try:
            avail = float(future_latest.get("available_funds") or 0)
        except (TypeError, ValueError):
            avail = 0.0

        ts = future_latest.get("timestamp")
        if ts is not None:
            timestamps.append(str(ts))

        if formula_terms:
            row_remark = "空头市值 = " + " + ".join(formula_terms)
        else:
            row_remark = f"保证金占用 {_fmt_num(future_latest.get('margin_used'))}"

        future_rows.append(
            {
                "account": coll,
                "market_value": _fmt_num(contrib),
                "available_funds": _fmt_num(avail),
                "remark": row_remark,
            }
        )

    future_ts = "；".join(timestamps) if timestamps else "-"
    future_market_value = future_market_value_total
    future_ratio = None
    try:
        if stock_market_value and float(stock_market_value) > 0:
            # 对冲比例口径：空头绝对规模 / 多头规模
            future_ratio = abs(float(future_market_value)) / float(stock_market_value) * 100.0
    except Exception:
        future_ratio = None

    target_ratio = 100.0
    ratio_deviation = None
    net_exposure_ratio = None
    # 对冲比例：与目标比较，低于目标绿、高于目标红
    hedge_ratio_class = "mn-hedge-neutral"
    # 对冲偏差：按绝对值阈值分档
    hedge_deviation_class = "mn-hedge-neutral"
    # 净敞口比例：正=净多(红)，负=净空(绿)，零=中性(灰)
    net_exposure_class = "mn-hedge-neutral"
    try:
        if (
            future_ratio is not None
            and stock_market_value is not None
            and float(stock_market_value) > 0
        ):
            ratio_deviation = float(future_ratio) - target_ratio
            if ratio_deviation > 1e-9:
                hedge_ratio_class = "mn-hedge-above-target"
            elif ratio_deviation < -1e-9:
                hedge_ratio_class = "mn-hedge-below-target"
            dev_abs = abs(float(ratio_deviation))
            if dev_abs <= 5.0 + 1e-9:
                hedge_deviation_class = "mn-hedge-below-target"
            elif dev_abs <= 15.0 + 1e-9:
                hedge_deviation_class = "mn-hedge-warn"
            else:
                hedge_deviation_class = "mn-hedge-above-target"

            stock_mv = float(stock_market_value)
            short_mv_abs = abs(float(future_market_value))
            net_exposure_ratio = (stock_mv - short_mv_abs) / stock_mv * 100.0
            if net_exposure_ratio > 1e-9:
                net_exposure_class = "mn-hedge-above-target"
            elif net_exposure_ratio < -1e-9:
                net_exposure_class = "mn-hedge-below-target"
    except Exception:
        ratio_deviation = None
        net_exposure_ratio = None

    stock_row = {
        "account": stock_collection,
        "market_value": _fmt_num(stock_market_value),
        "available_funds": _fmt_num(stock_available_cash),
        "remark": stock_remark,
        "hedge_ratio": "-",
        "appendix": f"采集时间: {stock_ts}",
        "target_hedge_ratio": _fmt_pct(target_ratio),
        "error": "-",
    }
    hedge_meta = {
        "hedge_ratio": _fmt_pct(future_ratio),
        "target_hedge_ratio": _fmt_pct(target_ratio),
        "hedge_deviation": _fmt_pct(ratio_deviation) if ratio_deviation is not None else "-",
        "net_exposure_ratio": _fmt_pct(net_exposure_ratio) if net_exposure_ratio is not None else "-",
        "hedge_ratio_class": hedge_ratio_class,
        "hedge_deviation_class": hedge_deviation_class,
        "net_exposure_class": net_exposure_class,
        "snapshot_ts": future_ts,
    }
    return stock_row, future_rows, hedge_meta


def _extract_latest_market_neutral_snapshot() -> dict[str, Any]:
    client = get_mongo_client()
    try:
        products: list[dict[str, Any]] = []
        for name, fut_coll, stk_coll in (
            # (
            #     "吾执二二号",
            #     "GMQH_59000028",
            #     "SWZQ_1673088777",
            # ),
            (
                "博士一号",
                ("HTQH_80017209",),
                "HTZQ_666810103835",
            ),
            (
                "吾执一三号",
                ("GTQH_8010101721", "WKQH_66601096"),
                "ZSZQ_911600210",
            ),
            (
                "吾执泽鑫多维",
                ("WKQH_66601123", "CJQH_81801575"),
                "GHZQ_17190083",
            ),
            ("模拟盘一号", 
                ("simnow_094287",), 
                "GJZQ_86014577",
            ),
        ):
            stock_row, future_rows, hedge_meta = _build_market_neutral_pair(
                fut_coll, stk_coll, client
            )
            products.append(
                {
                    "name": name,
                    "body_rowspan": 1 + len(future_rows),
                    "stock_row": stock_row,
                    "future_rows": future_rows,
                    **hedge_meta,
                }
            )
    finally:
        client.close()

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {"generated_at": generated_at, "products": products}


@login_required(login_url="/")
def placeholder_market_neutral(request):
    snapshot = _extract_latest_market_neutral_snapshot()
    return render(
        request,
        "portal/market_neutral_product.html",
        {
            "page_title": "对冲中性产品",
            "breadcrumb_label": "对冲中性产品",
            **snapshot,
        },
    )

