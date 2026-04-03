import json
import time
from datetime import datetime

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from portal.data.alpha_daily_schema import ALPHA_DAILY_COLUMNS
from portal.formatters import row_to_display_cells
from portal.mongo_queries import (
    ALPHA_DAILY_SORT,
    build_alpha_daily_query,
    fetch_alpha_daily_documents,
)


def index(request):
    """未登录：展示登录页；已登录：进入门户 /home/。"""
    if request.user.is_authenticated:
        return redirect("portal:home")

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.GET.get("next") or "/home/"
            if not next_url.startswith("/"):
                next_url = "/home/"
            return redirect(next_url)
        return render(
            request,
            "portal/login.html",
            {"error": "用户名或密码错误，请重试。"},
            status=401,
        )

    return render(request, "portal/login.html")


@login_required(login_url="/")
def home(request):
    """登录成功后的门户页：功能入口列表。"""
    return render(request, "portal/home.html")


@login_required(login_url="/")
def alpha_daily(request):
    """Alpha 产品日报：从 MongoDB（testdb.appdb）查询 _schema=alpha_daily 并表格展示。"""

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

    # 表格列：中文表头 + 英文字段顺序（与导入一致）
    field_keys = [en for _cn, en in ALPHA_DAILY_COLUMNS]
    headers_zh = [cn for cn, _en in ALPHA_DAILY_COLUMNS]

    table_rows: list[list[str]] = []
    for doc in rows:
        table_rows.append(row_to_display_cells(doc, field_keys))

    debug_info_text: str | None = None
    if show_debug and error_msg is None and elapsed_ms is not None:
        dbg = {
            "mongo_query": build_alpha_daily_query(
                date_from or None, date_to or None
            ),
            "sort": ALPHA_DAILY_SORT,
            "limit": limit,
            "elapsed_ms": round(elapsed_ms, 3),
            "row_count": len(rows),
        }
        debug_info_text = json.dumps(
            dbg, ensure_ascii=False, indent=2, default=str
        )

    context = {
        "headers_zh": headers_zh,
        "field_keys": field_keys,
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


def logout_view(request):
    logout(request)
    return redirect("portal:index")
