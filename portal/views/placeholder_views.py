"""占位页面视图（待补充业务内容）。"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required(login_url="/")
def placeholder_t0(request):
    return render(
        request,
        "portal/placeholder_page.html",
        {"page_title": "T0表现", "breadcrumb_label": "T0表现"},
    )


@login_required(login_url="/")
def placeholder_timing(request):
    return render(
        request,
        "portal/placeholder_page.html",
        {"page_title": "择时指标", "breadcrumb_label": "择时指标"},
    )


@login_required(login_url="/")
def placeholder_volatility(request):
    return render(
        request,
        "portal/placeholder_page.html",
        {"page_title": "波动率指标", "breadcrumb_label": "波动率指标"},
    )


@login_required(login_url="/")
def placeholder_commodity(request):
    return render(
        request,
        "portal/placeholder_page.html",
        {"page_title": "商品板块指标", "breadcrumb_label": "商品板块指标"},
    )
