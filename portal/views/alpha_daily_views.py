"""Alpha 日报页面与接口视图（Alpha Daily Views）。"""

import json
import time
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from portal.data.alpha_daily_schema import ALPHA_DAILY_COLUMNS
from portal.db.queries import (
    ALPHA_DAILY_SORT,
    build_alpha_daily_query,
    fetch_alpha_daily_documents,
)
from portal.services.formatters import row_to_display_cells
from portal.services.import_service import import_excel_fileobj


def _alpha_daily_visible_field_keys(request) -> list[str]:
    allowed_order = [en for _cn, en in ALPHA_DAILY_COLUMNS]
    allowed_set = frozenset(allowed_order)
    raw = [x.strip() for x in request.GET.getlist("col") if x.strip()]
    if not raw:
        return allowed_order
    picked = [en for en in allowed_order if en in allowed_set and en in set(raw)]
    return picked if picked else allowed_order


@login_required(login_url="/")
def alpha_daily(request):
    """Alpha 日报列表页视图。"""
    try:
        limit = int(request.GET.get("limit") or 100)
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 10000))

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    alpha_daily_debug_enabled = getattr(settings, "ALPHA_DAILY_PAGE_DEBUG", False)
    show_debug = alpha_daily_debug_enabled and request.GET.get("debug") == "1"

    error_msg = None
    rows: list[dict] = []
    elapsed_ms: float | None = None
    try:
        if date_from:
            datetime.strptime(date_from, "%Y-%m-%d")
        if date_to:
            datetime.strptime(date_to, "%Y-%m-%d")
    except ValueError:
        error_msg = "日期格式须为 YYYY-MM-DD（请使用下方日期选择器）。"
    else:
        try:
            t0 = time.perf_counter()
            rows = fetch_alpha_daily_documents(
                limit=limit,
                date_from=date_from or None,
                date_to=date_to or None,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        except Exception as exc:
            error_msg = f"读取 MongoDB 失败：{exc}"

    visible_field_keys = _alpha_daily_visible_field_keys(request)
    cn_by_en = {en: cn for cn, en in ALPHA_DAILY_COLUMNS}
    headers_zh = [cn_by_en[en] for en in visible_field_keys]
    column_catalog = [
        {"cn": cn, "en": en, "checked": en in set(visible_field_keys)}
        for cn, en in ALPHA_DAILY_COLUMNS
    ]

    table_rows: list[list[str]] = []
    for doc in rows:
        table_rows.append(row_to_display_cells(doc, visible_field_keys))

    debug_info_text: str | None = None
    if show_debug and error_msg is None and elapsed_ms is not None:
        dbg = {
            "mongo_query": build_alpha_daily_query(date_from or None, date_to or None),
            "sort": ALPHA_DAILY_SORT,
            "limit": limit,
            "elapsed_ms": round(elapsed_ms, 3),
            "row_count": len(rows),
        }
        debug_info_text = json.dumps(dbg, ensure_ascii=False, indent=2, default=str)

    context = {
        "headers_zh": headers_zh,
        "column_catalog": column_catalog,
        "table_rows": table_rows,
        "raw_count": len(rows),
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
        "error_msg": error_msg,
        "alpha_daily_debug_enabled": alpha_daily_debug_enabled,
        "show_debug": show_debug,
        "debug_info_text": debug_info_text,
    }
    return render(request, "portal/alpha_daily.html", context)


@login_required(login_url="/")
def alpha_daily_import(request):
    """Alpha 日报上传导入视图。"""
    if request.method != "POST":
        return redirect("portal:alpha_daily")
    up = request.FILES.get("xlsx_file")
    if up is None:
        messages.error(request, "请选择 xlsx 文件后再导入。")
        return redirect("portal:alpha_daily")
    try:
        stats = import_excel_fileobj(up, up.name)
    except Exception as exc:
        messages.error(request, f"导入失败：{exc}")
        return redirect("portal:alpha_daily")
    messages.success(request, f"导入完成：{stats['file']}，写入 {stats['inserted']} 条。")
    return redirect("portal:alpha_daily")


def _parse_common_query_params(request):
    try:
        limit = int(request.GET.get("limit") or 100)
    except ValueError:
        raise ValueError("limit 必须为整数")
    limit = max(1, min(limit, 10000))
    date_from = (request.GET.get("date_from") or "").strip() or None
    date_to = (request.GET.get("date_to") or "").strip() or None
    if date_from:
        datetime.strptime(date_from, "%Y-%m-%d")
    if date_to:
        datetime.strptime(date_to, "%Y-%m-%d")
    return limit, date_from, date_to


@csrf_exempt
def api_alpha_daily(request):
    """Alpha 日报查询 API 视图。"""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "仅支持 GET"}, status=405)
    try:
        limit, date_from, date_to = _parse_common_query_params(request)
        rows = fetch_alpha_daily_documents(
            limit=limit,
            date_from=date_from,
            date_to=date_to,
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": f"查询失败: {exc}"}, status=500)
    return JsonResponse(
        {
            "ok": True,
            "count": len(rows),
            "limit": limit,
            "query": build_alpha_daily_query(date_from, date_to),
            "sort": ALPHA_DAILY_SORT,
            "data": rows,
        },
        json_dumps_params={"ensure_ascii": False},
    )


@csrf_exempt
def api_alpha_import(request):
    """Alpha 日报导入 API 视图。"""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "仅支持 POST"}, status=405)
    up = request.FILES.get("file") or request.FILES.get("xlsx_file")
    if up is None:
        return JsonResponse(
            {"ok": False, "error": "缺少文件字段 file（或 xlsx_file）"},
            status=400,
        )
    try:
        stats = import_excel_fileobj(up, up.name)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": f"导入失败: {exc}"}, status=500)
    return JsonResponse({"ok": True, **stats}, json_dumps_params={"ensure_ascii": False})

