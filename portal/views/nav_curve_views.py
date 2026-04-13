"""净值曲线相关页面与接口视图（Nav Curve Views）。"""

import json
import time
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from portal.data.alpha_daily_schema import is_alpha_daily_product_name_excluded
from portal.data.fund_nav_real_config import FUND_NAV_PORTAL_COLUMNS, FUND_NAV_PRODUCTS
from portal.db.fund_nav_queries import (
    build_fund_nav_mongo_query,
    fetch_fund_nav_portal_documents,
    fund_nav_product_keys_from_request,
)
from portal.services.formatters import row_to_fund_nav_display_cells
from portal.services.trade_calendar_service import (
    distinct_product_names,
    fetch_nav_curve_series,
)


def _parse_only_trading_days(request) -> bool:
    if "only_trading_days" not in request.GET:
        return True
    v = (request.GET.get("only_trading_days") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _parse_recent_trading_days(request) -> tuple[int | None, str]:
    raw = (request.GET.get("recent") or "").strip()
    if raw in ("5", "10", "15"):
        return int(raw), raw
    return None, raw


def _parse_nav_time_mode(request) -> str:
    raw = (request.GET.get("time_mode") or "").strip().lower()
    if raw == "custom":
        return "custom"
    return "recent"


def _fund_nav_visible_field_keys(request) -> list[str]:
    allowed_order = [en for _cn, en in FUND_NAV_PORTAL_COLUMNS]
    allowed_set = frozenset(allowed_order)
    raw = [x.strip() for x in request.GET.getlist("col") if x.strip()]
    if not raw:
        return allowed_order
    picked = [en for en in allowed_order if en in allowed_set and en in set(raw)]
    return picked if picked else allowed_order


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
    """净值曲线页面视图。"""
    products = distinct_product_names()
    selected_products = [p.strip() for p in request.GET.getlist("product_name") if p.strip()]
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
        datasets.append({"label": pn, "data": [nav_map.get(day) for day in labels]})

    chart_json = json.dumps({"labels": labels, "datasets": datasets}, ensure_ascii=False)

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
    """基金净值页：展示 fund_nav_real 下 WZ_BSYH_MASTER / WZ_BSYH_B 净值表数据。"""
    try:
        limit = int(request.GET.get("limit") or 200)
    except ValueError:
        limit = 200
    limit = max(1, min(limit, 10000))

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    fund_nav_debug_enabled = getattr(settings, "FUND_NAV_PAGE_DEBUG", False)
    show_debug = fund_nav_debug_enabled and request.GET.get("debug") == "1"

    form_submitted = (request.GET.get("nav_q") or "").strip() == "1"
    product_keys = fund_nav_product_keys_from_request(request.GET, form_submitted=form_submitted)

    if product_keys is None:
        fund_nav_selection = None
    else:
        fund_nav_selection = frozenset(product_keys)

    fund_nav_empty_product_pick = (
        form_submitted and product_keys is not None and len(product_keys) == 0
    )

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
            rows = fetch_fund_nav_portal_documents(
                limit=limit,
                date_from=date_from or None,
                date_to=date_to or None,
                product_keys=product_keys,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        except Exception as exc:
            error_msg = f"读取 MongoDB 失败：{exc}"

    visible_field_keys = _fund_nav_visible_field_keys(request)
    cn_by_en = {en: cn for cn, en in FUND_NAV_PORTAL_COLUMNS}
    headers_zh = [cn_by_en[en] for en in visible_field_keys]
    column_catalog = [
        {"cn": cn, "en": en, "checked": en in set(visible_field_keys)}
        for cn, en in FUND_NAV_PORTAL_COLUMNS
    ]

    table_rows: list[list[str]] = []
    for doc in rows:
        table_rows.append(row_to_fund_nav_display_cells(doc, visible_field_keys))

    debug_info_text: str | None = None
    if show_debug and error_msg is None and elapsed_ms is not None:
        dbg = {
            "mongo_query_per_collection": build_fund_nav_mongo_query(
                date_from or None, date_to or None
            ),
            "product_keys": product_keys,
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
        "fund_nav_products": FUND_NAV_PRODUCTS,
        "fund_nav_selection": fund_nav_selection,
        "fund_nav_empty_product_pick": fund_nav_empty_product_pick,
        "fund_nav_debug_enabled": fund_nav_debug_enabled,
        "show_debug": show_debug,
        "debug_info_text": debug_info_text,
        "fund_nav_mongo_db": settings.MONGODB_FUND_NAV_REAL_DB,
        "fund_nav_coll_master": settings.NAV_REAL_WZ_BSYH_MASTER,
        "fund_nav_coll_b": settings.NAV_REAL_WZ_BSYH_B,
    }
    return render(request, "portal/raw_nav.html", context)


@csrf_exempt
def api_nav_curve(request):
    """净值曲线查询 API 视图。"""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "仅支持 GET"}, status=405)
    product_name = (request.GET.get("product_name") or "").strip()
    if not product_name:
        return JsonResponse({"ok": False, "error": "缺少 product_name"}, status=400)
    if is_alpha_daily_product_name_excluded(product_name):
        return JsonResponse({"ok": False, "error": "该产品不在展示范围内"}, status=400)

    date_from = (request.GET.get("date_from") or "").strip() or None
    date_to = (request.GET.get("date_to") or "").strip() or None
    only_td = _parse_only_trading_days(request)
    recent_raw = (request.GET.get("recent") or "").strip()

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

