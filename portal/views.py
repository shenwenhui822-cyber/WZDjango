import json
import time
from datetime import datetime

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import redirect, render

from portal.data.alpha_daily_schema import ALPHA_DAILY_COLUMNS
from portal.formatters import row_to_display_cells
from portal.import_service import import_excel_fileobj
from portal.mongo_queries import (
    ALPHA_DAILY_SORT,
    build_alpha_daily_query,
    fetch_alpha_daily_documents,
)
from portal.trade_calendar import (
    distinct_product_names,
    fetch_nav_curve_series,
)


def _alpha_daily_visible_field_keys(request) -> list[str]:
    """
    从 GET 解析要展示的列（英文字段名，与 ALPHA_DAILY_COLUMNS 一致）。
    未传 col 时默认展示全部；顺序固定为 schema 定义顺序。
    """
    allowed_order = [en for _cn, en in ALPHA_DAILY_COLUMNS]
    allowed_set = frozenset(allowed_order)
    raw = [x.strip() for x in request.GET.getlist("col") if x.strip()]
    if not raw:
        return allowed_order
    picked = [en for en in allowed_order if en in allowed_set and en in set(raw)]
    return picked if picked else allowed_order


def index(request):
    """未登录：展示登录页；已登录：进入首页 /home/。"""
    if request.user.is_authenticated:
        return redirect("portal:nav_curve")

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.GET.get("next") or "/nav/curve/"
            if not next_url.startswith("/"):
                next_url = "/nav/curve/"
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
    """登录成功后的首页：功能入口列表。"""
    return redirect("portal:nav_curve")


@login_required(login_url="/")
def alpha_daily(request):
    """Alpha 产品日报：从 MongoDB（alpha_product.alpha_sim_nav）查询 _schema=alpha_daily 并表格展示。"""

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
    """网页上传 xlsx 并写入 MongoDB。"""
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
    messages.success(
        request,
        f"导入完成：{stats['file']}，写入 {stats['inserted']} 条。",
    )
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
    """GET: 查询 alpha_daily 数据，返回 JSON。"""
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
    """POST: 上传单个 xlsx 文件并导入 MongoDB。"""
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
    return JsonResponse(
        {"ok": True, **stats},
        json_dumps_params={"ensure_ascii": False},
    )


def _parse_only_trading_days(request) -> bool:
    if "only_trading_days" not in request.GET:
        return True
    v = (request.GET.get("only_trading_days") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _parse_recent_trading_days(request) -> tuple[int | None, str]:
    """返回 (recent_n 或 None, 原始字符串用于表单回显)。"""
    raw = (request.GET.get("recent") or "").strip()
    if raw in ("5", "10", "15"):
        return int(raw), raw
    return None, raw


def _parse_nav_time_mode(request) -> str:
    raw = (request.GET.get("time_mode") or "").strip().lower()
    if raw == "custom":
        return "custom"
    return "recent"


def _parse_date_range_for_nav(request) -> tuple[str | None, str | None]:
    date_from = (request.GET.get("date_from") or "").strip() or None
    date_to = (request.GET.get("date_to") or "").strip() or None
    try:
        if date_from:
            datetime.strptime(date_from, "%Y-%m-%d")
        if date_to:
            datetime.strptime(date_to, "%Y-%m-%d")
    except ValueError:
        raise ValueError("日期须为 YYYY-MM-DD。") from None
    if date_from and date_to and date_from > date_to:
        raise ValueError("开始日期不能晚于结束日期。")
    return date_from, date_to


@login_required(login_url="/")
def nav_curve(request):
    """多产品净值曲线：支持多选产品，最近交易日或自定义日期。"""
    products = distinct_product_names()
    selected_products = [
        p.strip() for p in request.GET.getlist("product_name") if p.strip()
    ]
    if not selected_products and products:
        selected_products = [products[0]]

    time_mode = _parse_nav_time_mode(request)
    only_td = _parse_only_trading_days(request)

    recent_n, recent_raw = _parse_recent_trading_days(request)
    if recent_n is None:
        recent_n = 5
        recent_raw = "5"

    date_from_ctx = ""
    date_to_ctx = ""

    error_msg = None
    product_series: dict[str, list[dict]] = {}
    if not products:
        error_msg = "库中暂无产品数据，请先导入 Alpha 日报。"
    elif not selected_products:
        error_msg = "请选择至少一个产品。"
    else:
        try:
            picked = [p for p in selected_products if p in products]
            if not picked:
                raise ValueError("所选产品无效，请重新选择。")
            if len(picked) > 10:
                raise ValueError("一次最多选择 10 个产品。")
            if time_mode == "custom":
                date_from, date_to = _parse_date_range_for_nav(request)
                date_from_ctx = date_from or ""
                date_to_ctx = date_to or ""
                if not date_from and not date_to:
                    error_msg = "自定义模式下请至少填写开始日期或结束日期。"
                else:
                    for pn in picked:
                        product_series[pn] = fetch_nav_curve_series(
                            product_name=pn,
                            date_from=date_from,
                            date_to=date_to,
                            only_trading_days=only_td,
                            recent_trading_days=None,
                        )
            else:
                for pn in picked:
                    product_series[pn] = fetch_nav_curve_series(
                        product_name=pn,
                        date_from=None,
                        date_to=None,
                        only_trading_days=only_td,
                        recent_trading_days=recent_n,
                    )
        except ValueError as exc:
            error_msg = str(exc)
            if time_mode == "custom":
                date_from_ctx = (request.GET.get("date_from") or "").strip()
                date_to_ctx = (request.GET.get("date_to") or "").strip()
        except Exception as exc:
            error_msg = str(exc)

    labels = sorted(
        {
            p["report_date"]
            for series in product_series.values()
            for p in series
            if p.get("report_date")
        }
    )
    datasets: list[dict] = []
    point_count = 0
    for pn, series in product_series.items():
        nav_map = {p["report_date"]: p["current_nav"] for p in series}
        point_count += len(series)
        datasets.append(
            {
                "label": pn,
                "data": [nav_map.get(day) for day in labels],
            }
        )

    chart_json = json.dumps(
        {
            "labels": labels,
            "datasets": datasets,
        },
        ensure_ascii=False,
    )

    context = {
        "error_msg": error_msg,
        "products": products,
        "selected_products": selected_products,
        "time_mode": time_mode,
        "recent": recent_raw,
        "date_from": date_from_ctx,
        "date_to": date_to_ctx,
        "only_trading_days": only_td,
        "point_count": point_count,
        "chart_json": chart_json,
    }
    return render(request, "portal/nav_curve.html", context)


@login_required(login_url="/")
def raw_nav(request):
    """原始净值占位页：等待接入产品原始净值数据。"""
    return render(request, "portal/raw_nav.html")


@login_required(login_url="/")
def t0_nav(request):
    """T0 增强测算占位页：等待接入测算数据。"""
    return render(request, "portal/t0_nav.html")


@csrf_exempt
def api_nav_curve(request):
    """GET: 净值曲线 JSON。recent=5|10|15 为最近 N 个交易日；若未传 recent 且未传日期区间则默认 recent=5。"""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "仅支持 GET"}, status=405)
    product_name = (request.GET.get("product_name") or "").strip()
    if not product_name:
        return JsonResponse({"ok": False, "error": "缺少 product_name"}, status=400)

    date_from = (request.GET.get("date_from") or "").strip() or None
    date_to = (request.GET.get("date_to") or "").strip() or None
    only_td = _parse_only_trading_days(request)
    recent_raw = (request.GET.get("recent") or "").strip()

    use_recent: int | None
    if recent_raw in ("5", "10", "15"):
        use_recent = int(recent_raw)
    elif date_from or date_to:
        use_recent = None
    else:
        use_recent = 5

    try:
        if use_recent is not None:
            points = fetch_nav_curve_series(
                product_name=product_name,
                date_from=None,
                date_to=None,
                only_trading_days=only_td,
                recent_trading_days=use_recent,
            )
        else:
            if date_from:
                datetime.strptime(date_from, "%Y-%m-%d")
            if date_to:
                datetime.strptime(date_to, "%Y-%m-%d")
            points = fetch_nav_curve_series(
                product_name=product_name,
                date_from=date_from,
                date_to=date_to,
                only_trading_days=only_td,
                recent_trading_days=None,
            )
    except ValueError:
        return JsonResponse({"ok": False, "error": "日期须为 YYYY-MM-DD"}, status=400)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)
    return JsonResponse(
        {
            "ok": True,
            "count": len(points),
            "recent": use_recent,
            "only_trading_days": only_td,
            "series": points,
        },
        json_dumps_params={"ensure_ascii": False},
    )


def logout_view(request):
    logout(request)
    return redirect("portal:index")
