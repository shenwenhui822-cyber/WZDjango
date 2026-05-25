"""股指期货 ETF 指标页：option.volatility。"""
from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from portal.services.option_volatility_service import (
    ETF_METRIC_DEFINITIONS,
    ETF_METRIC_KEYS,
    format_metric_display,
    load_etf_latest_snapshot,
    load_etf_volatility_series,
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
) -> dict:
    error: str | None = None
    chart_data: dict = {"labels": [], "datasets": []}
    summary: dict = {}
    selected_label = selected_metric
    indicators: list[dict] = []

    try:
        snap = load_etf_latest_snapshot(limit=limit)
        latest_by_key = snap.get("latest_by_key") or {}
        latest_date = snap.get("latest_date") or ""
        indicators = _build_indicators(latest_by_key, latest_date=latest_date)

        series = load_etf_volatility_series(
            metric_key=selected_metric,
            recent_window=recent_window,
            limit=limit,
        )
        selected_label = series["summary"].get("metric_label") or selected_metric
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
    }


@login_required(login_url="/")
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

    ctx = _build_page_context(
        selected_metric=selected_metric,
        recent_raw=recent_raw,
        recent_window=recent_window,
        limit=lim,
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
            },
            json_dumps_params={"ensure_ascii": False},
        )

    return render(
        request,
        "portal/option_metrics.html",
        {
            "page_title": "股指期货",
            "breadcrumb_label": "股指期货",
            "generated_at": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
            **ctx,
        },
    )
