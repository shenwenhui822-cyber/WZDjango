"""期权指标页：option.volatility。"""
from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from portal.services.option_volatility_service import load_volatility_series


@login_required(login_url="/")
def option_metrics(request):
    error: str | None = None
    chart_data: dict = {"labels": [], "datasets": []}
    summary: dict = {}
    indicators: list[dict] = [
        {
            "key": "volatility_num",
            "label": "波动率",
            "unit": "",
            "description": "option.volatility.volatility_num",
        },
    ]
    selected_metric = (request.GET.get("metric") or "volatility_num").strip()
    if selected_metric not in {i["key"] for i in indicators}:
        selected_metric = "volatility_num"

    try:
        lim = int((request.GET.get("limit") or "5000").strip())
    except ValueError:
        lim = 5000

    try:
        series = load_volatility_series(limit=lim)
        summary = series.get("summary") or {}
        labels = series.get("labels") or []
        values = series.get("values") or []
        chart_data = {
            "labels": labels,
            "datasets": [
                {
                    "label": "波动率",
                    "data": values,
                }
            ],
        }
        for ind in indicators:
            if ind["key"] == "volatility_num":
                ind["latest_date"] = summary.get("latest_date") or "—"
                lv = summary.get("latest_value")
                ind["latest_value"] = (
                    f"{lv:.6g}" if isinstance(lv, (int, float)) else "—"
                )
    except Exception as exc:
        error = str(exc)

    return render(
        request,
        "portal/option_metrics.html",
        {
            "page_title": "期权指标",
            "breadcrumb_label": "期权指标",
            "generated_at": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
            "indicators": indicators,
            "selected_metric": selected_metric,
            "summary": summary,
            "chart_json": json.dumps(chart_data, ensure_ascii=False),
            "error": error,
            "point_count": summary.get("count", 0),
        },
    )
