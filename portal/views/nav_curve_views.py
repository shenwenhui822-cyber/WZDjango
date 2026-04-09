"""净值曲线相关页面与接口视图（Nav Curve Views）。"""

import json
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

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
    """原始净值页视图。"""
    return render(request, "portal/raw_nav.html")


@login_required(login_url="/")
def t0_nav(request):
    """T0 净值页视图。"""
    return render(request, "portal/t0_nav.html")


@csrf_exempt
def api_nav_curve(request):
    """净值曲线查询 API 视图。"""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "仅支持 GET"}, status=405)
    product_name = (request.GET.get("product_name") or "").strip()
    if not product_name:
        return JsonResponse({"ok": False, "error": "缺少 product_name"}, status=400)

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

