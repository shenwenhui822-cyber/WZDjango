"""账户简报：从 position_close_record 读取收盘快照。"""
from __future__ import annotations

from portal.auth_access import PERM_VIEW_ACCOUNT_BRIEF, portal_page_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from portal.services.position_close_record_service import build_account_brief_page_context


@portal_page_required(PERM_VIEW_ACCOUNT_BRIEF)
def account_brief(request):
    ctx = build_account_brief_page_context(
        selected_tag=(request.GET.get("account") or "").strip() or None,
        snapshot_date=(request.GET.get("snapshot_date") or "").strip() or None,
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse(
            {"ok": True, **ctx},
            json_dumps_params={"ensure_ascii": False},
        )

    return render(
        request,
        "portal/account_brief.html",
        {
            "page_title": "账户简报",
            "breadcrumb_label": "账户简报",
            "generated_at": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
            **ctx,
        },
    )
