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
def placeholder_market_neutral(request):
    return render(
        request,
        "portal/placeholder_page.html",
        {"page_title": "对冲中性产品", "breadcrumb_label": "对冲中性产品"},
    )

