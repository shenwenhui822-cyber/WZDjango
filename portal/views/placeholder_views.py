"""占位页面视图（待补充业务内容）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from portal.db.mongo import get_mongo_client

FUTURE_POINT_MULTIPLIER: dict[str, int] = {
    "IF": 300,
    "IH": 300,
    "IC": 200,
    "IM": 200,
}


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


def _extract_latest_market_neutral_snapshot() -> dict[str, Any]:
    client = get_mongo_client()
    try:
        future_doc = (
            client["rt_future"]["simnow_094287"]
            .find({}, {"_id": 0})
            .sort([("timestamp", -1), ("_id", -1)])
            .limit(1)
        )
        future_latest = next(iter(future_doc), {})

        stock_doc = (
            client["rt_stock"]["GJZQ_86014577"]
            .find({}, {"_id": 0})
            .sort([("ts", -1), ("date", -1), ("time", -1), ("_id", -1)])
            .limit(1)
        )
        stock_latest = next(iter(stock_doc), {})
    finally:
        client.close()

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

    positions = future_latest.get("positions") or []
    pos_lines: list[str] = []
    formula_terms: list[str] = []
    future_notional_value = 0.0
    for p in positions[:10]:
        contract = p.get("contract") or "-"
        direction = p.get("direction") or "-"
        total_position = int(p.get("total_position") or 0)
        avg_px_raw = p.get("average_opening_price") or 0
        avg_price = _fmt_num(avg_px_raw, digits=2)
        contract_upper = str(contract).upper()
        prefix = "".join(ch for ch in contract_upper if ch.isalpha())[:2]
        multi = FUTURE_POINT_MULTIPLIER.get(prefix)
        if multi and avg_px_raw:
            term_value = abs(float(avg_px_raw)) * abs(total_position) * multi
            future_notional_value += term_value
            formula_terms.append(
                f"{contract}({direction}) {total_position}*{_fmt_num(avg_px_raw, 2)}*{multi}"
            )
        pos_lines.append(f"{contract} {direction} {total_position}手 @ {avg_price}")
    if len(positions) > 10:
        pos_lines.append(f"... 其余 {len(positions) - 10} 条持仓")
    future_appendix = "；".join(pos_lines) if pos_lines else "-"

    future_market_value = future_notional_value if future_notional_value > 0 else (
        future_latest.get("margin_used") or 0
    )
    future_available_cash = future_latest.get("available_funds")
    future_ts = future_latest.get("timestamp") or "-"
    future_ratio = None
    try:
        if stock_market_value and float(stock_market_value) > 0:
            future_ratio = float(future_market_value) / float(stock_market_value) * 100.0
    except Exception:
        future_ratio = None

    target_ratio = 100.0
    ratio_deviation = None
    ratio_deviation_abs = None
    ratio_color = "text-muted"
    try:
        if future_ratio is not None:
            ratio_deviation = float(future_ratio) - target_ratio
            ratio_deviation_abs = abs(ratio_deviation)
            if ratio_deviation > 0:
                ratio_color = "text-danger"
            elif ratio_deviation < 0:
                ratio_color = "text-success"
    except Exception:
        ratio_deviation = None
        ratio_deviation_abs = None

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "generated_at": generated_at,
        "stock_row": {
            "account": f"GJZQ_{stock_latest.get('account_id') or '86014577'}",
            "market_value": _fmt_num(stock_market_value),
            "available_funds": _fmt_num(stock_available_cash),
            "remark": stock_remark,
            "hedge_ratio": "-",
            "appendix": f"采集时间: {stock_ts}",
            "target_hedge_ratio": _fmt_pct(target_ratio),
            "error": "-",
        },
        "future_row": {
            "account": "simnow_094287",
            "market_value": _fmt_num(future_market_value),
            "available_funds": _fmt_num(future_available_cash),
            "remark": (
                "总市值 = " + " + ".join(formula_terms)
                if formula_terms
                else f"保证金占用 {_fmt_num(future_latest.get('margin_used'))}"
            ),
            "hedge_ratio": _fmt_pct(future_ratio),
            "appendix": future_appendix,
            "target_hedge_ratio": _fmt_pct(target_ratio),
            "hedge_deviation": _fmt_pct(ratio_deviation_abs) if ratio_deviation_abs is not None else "-",
            "ratio_color": ratio_color,
            "snapshot_ts": future_ts,
        },
    }


@login_required(login_url="/")
def placeholder_t0(request):
    return render(
        request,
        "portal/placeholder_page.html",
        {"page_title": "T0表现", "breadcrumb_label": "T0表现"},
    )


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

