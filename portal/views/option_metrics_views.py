"""股指期货 ETF 指标页：option.volatility。"""
from __future__ import annotations

import json

from portal.auth_access import PERM_VIEW_OPTION_METRICS, portal_page_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from portal.services.option_volatility_service import (
    DEFAULT_TABLE_DISPLAY_LIMIT,
    ETF_METRIC_DEFINITIONS,
    ETF_METRIC_KEYS,
    format_metric_display,
    load_etf_volatility_bundle,
    parse_recent_window,
)


def _build_indicators(latest_by_key: dict, *, latest_date: str) -> list[dict]:
    items: list[dict] = []
    for key, label in ETF_METRIC_DEFINITIONS:
        lv = latest_by_key.get(key)
        items.append(
            {
                "key": key,
                "label": label,
                "description": f"option.volatility.{key}",
                "latest_value": format_metric_display(lv),
                "latest_date": latest_date or "",
            }
        )
    return items


def _build_page_context(
    *,
    selected_metric: str,
    recent_raw: str,
    recent_window: int,
    limit: int,
    table_limit: int,
) -> dict:
    error: str | None = None
    chart_data: dict = {"labels": [], "datasets": []}
    summary: dict = {}
    selected_label = selected_metric
    indicators: list[dict] = []

    table_headers_zh: list[str] = []
    table_rows: list[list[str]] = []
    table_column_keys: list[str] = []
    table_total_count = 0

    try:
        bundle = load_etf_volatility_bundle(
            metric_key=selected_metric,
            recent_window=recent_window,
            limit=limit,
            table_limit=table_limit,
        )
        latest_by_key = bundle.get("latest_by_key") or {}
        latest_date = bundle.get("latest_date") or ""
        indicators = _build_indicators(latest_by_key, latest_date=latest_date)

        series = bundle.get("series") or {}
        selected_label = series.get("summary", {}).get("metric_label") or selected_metric
        summary = series.get("summary") or {}
        chart_data = {
            "labels": series.get("labels") or [],
            "datasets": [
                {
                    "label": selected_label,
                    "data": series.get("values") or [],
                }
            ],
        }
        table_headers_zh = bundle.get("table_headers_zh") or []
        table_rows = bundle.get("table_rows") or []
        table_column_keys = bundle.get("table_column_keys") or []
        table_total_count = int(bundle.get("table_total_count") or len(table_rows))
        for ind in indicators:
            if ind["key"] == selected_metric:
                ind["latest_value"] = format_metric_display(
                    summary.get("latest_value")
                )
    except Exception as exc:
        error = str(exc)
        indicators = [
            {
                "key": key,
                "label": label,
                "description": f"option.volatility.{key}",
                "latest_value": "—",
                "latest_date": "",
            }
            for key, label in ETF_METRIC_DEFINITIONS
        ]

    return {
        "error": error,
        "indicators": indicators,
        "selected_metric": selected_metric,
        "selected_metric_label": selected_label,
        "summary": summary,
        "chart_json": json.dumps(chart_data, ensure_ascii=False),
        "point_count": summary.get("count", 0),
        "recent": recent_raw,
        "table_headers_zh": table_headers_zh,
        "table_rows": table_rows,
        "table_column_keys": table_column_keys,
        "table_row_count": len(table_rows),
        "table_total_count": table_total_count,
        "table_limit": table_limit,
    }


@portal_page_required(PERM_VIEW_OPTION_METRICS)
def option_metrics(request):
    default_metric = ETF_METRIC_DEFINITIONS[0][0]
    selected_metric = (request.GET.get("metric") or default_metric).strip()
    if selected_metric not in ETF_METRIC_KEYS:
        selected_metric = default_metric

    try:
        lim = int((request.GET.get("limit") or "5000").strip())
    except ValueError:
        lim = 5000
    lim = max(100, min(lim, 20_000))

    recent_window, recent_raw = parse_recent_window(request.GET.get("recent"))

    try:
        tbl_lim = int((request.GET.get("table_limit") or str(DEFAULT_TABLE_DISPLAY_LIMIT)).strip())
    except ValueError:
        tbl_lim = DEFAULT_TABLE_DISPLAY_LIMIT
    tbl_lim = max(0, min(tbl_lim, 5000))

    ctx = _build_page_context(
        selected_metric=selected_metric,
        recent_raw=recent_raw,
        recent_window=recent_window,
        limit=lim,
        table_limit=tbl_lim,
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse(
            {
                "ok": ctx["error"] is None,
                "error": ctx["error"],
                "selected_metric": selected_metric,
                "selected_metric_label": ctx["selected_metric_label"],
                "recent": recent_raw,
                "point_count": ctx["point_count"],
                "summary": ctx["summary"],
                "chart_data": json.loads(ctx["chart_json"]),
                "indicators": ctx["indicators"],
                "table_headers_zh": ctx["table_headers_zh"],
                "table_rows": ctx["table_rows"],
                "table_column_keys": ctx["table_column_keys"],
                "table_row_count": ctx["table_row_count"],
                "table_total_count": ctx["table_total_count"],
                "table_limit": ctx["table_limit"],
            },
            json_dumps_params={"ensure_ascii": False},
        )

    return render(
        request,
        "portal/option_metrics.html",
        {
            "page_title": "波动率指标",
            "breadcrumb_label": "波动率指标",
            "generated_at": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
            **ctx,
        },
    )
