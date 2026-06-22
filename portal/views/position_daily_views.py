"""日度持仓分析：portal 前端 + position_daily 后端数据。"""
from __future__ import annotations

from django.shortcuts import render

from portal.auth_access import PERM_VIEW_POSITION_DAILY, portal_page_required
from portal.services.position_daily_page_service import build_position_daily_page_context


def _render_report(request, *, trade_date: str | None = None):
    if request.GET.get("date"):
        trade_date = request.GET.get("date")
    strategy_tag = request.GET.get("account")
    force_refresh = request.GET.get("refresh") == "1"
    ctx = build_position_daily_page_context(
        strategy_tag=strategy_tag,
        trade_date=trade_date,
        force_refresh=force_refresh,
    )
    return render(
        request,
        "portal/position_daily_report.html",
        {
            "page_title": "日度持仓分析",
            "breadcrumb_label": "日度持仓分析",
            **ctx,
        },
    )


@portal_page_required(PERM_VIEW_POSITION_DAILY)
def index_view(request):
    return _render_report(request, trade_date="latest")


@portal_page_required(PERM_VIEW_POSITION_DAILY)
def report_view(request, trade_date=None):
    return _render_report(request, trade_date=trade_date)
