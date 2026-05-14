"""净值曲线相关页面与接口视图（Nav Curve Views）。"""

import json
import math
import time
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from portal.data.alpha_daily_schema import ALPHA_DAILY_COLUMNS, is_alpha_daily_product_name_excluded
from portal.data.fund_nav_real_config import (
    FUND_NAV_PORTAL_COLUMNS,
    FUND_NAV_PRODUCTS,
    fund_nav_portal_sidebar_products,
)
from portal.db.fund_nav_queries import (
    build_fund_nav_mongo_query,
    fetch_fund_nav_portal_documents,
    fund_nav_product_keys_from_raw_nav_request,
)
from portal.db.queries import fetch_alpha_daily_documents
from portal.services.benchmark_compare_service import build_and_store_nav_bench_compare
from portal.services.formatters import (
    ALPHA_COMPARE_TABLE_LEFT_ALIGN_EN,
    FUND_NAV_TABLE_LEFT_ALIGN_EN,
    row_to_display_cells,
    row_to_fund_nav_display_cells,
)
from portal.services.trade_calendar_service import (
    distinct_product_names,
    fetch_nav_curve_series,
)


def _zxdw_nav_product_keys() -> frozenset[str]:
    return frozenset(getattr(settings, "MONGODB_ZXDW_NAV_COLLECTIONS", ()))


_BENCH_COMPARE_GROUP_PREFIXES: list[str] = [
    "中证1000指增",
    "中证500指增",
    "双创选股",
    "尊选",
    "沪深300指增",
    "红利",
    "量化对冲",
    "量化选股",
]


def _group_compare_products(products: list[str], selected: str) -> list[dict]:
    grouped: list[dict] = []
    assigned: set[str] = set()
    for prefix in _BENCH_COMPARE_GROUP_PREFIXES:
        children = [p for p in products if p.startswith(prefix)]
        assigned.update(children)
        grouped.append(
            {
                "name": prefix,
                "products": children,
                "open": bool(selected and any(p == selected for p in children)),
            }
        )
    others = [p for p in products if p not in assigned]
    if others:
        grouped.append(
            {
                "name": "其他",
                "products": others,
                "open": bool(selected and selected in others),
            }
        )
    return grouped


def _build_fund_nav_chart_data(rows: list[dict], selected_keys: list[str] | None) -> dict:
    labels_set: set[str] = set()
    points_by_product: dict[str, dict[str, float | None]] = {}
    label_by_product: dict[str, str] = {}

    for doc in rows:
        nd = doc.get("nav_date")
        if hasattr(nd, "strftime"):
            day = nd.strftime("%Y-%m-%d")
        else:
            day = str(nd or "")[:10]
        if not day:
            continue
        labels_set.add(day)
        product_key = str(doc.get("product_key") or "")
        if not product_key:
            continue
        label_by_product[product_key] = str(
            doc.get("product_label")
            or doc.get("asset_name")
            or product_key
        )
        raw_nav = doc.get("cumulative_unit_nav")
        if product_key in _zxdw_nav_product_keys():
            if raw_nav is None:
                raw_nav = doc.get("unit_nav")
        else:
            if raw_nav is None:
                raw_nav = doc.get("cumulative_nav")
            if raw_nav is None:
                raw_nav = doc.get("unit_nav")
        try:
            nav_val = float(raw_nav) if raw_nav is not None else None
        except (TypeError, ValueError):
            nav_val = None
        points_by_product.setdefault(product_key, {})[day] = nav_val

    labels = sorted(labels_set)
    if selected_keys is None:
        product_order = [f["product_key"] for f in FUND_NAV_PRODUCTS]
    else:
        product_order = list(selected_keys)
    for k in points_by_product.keys():
        if k not in product_order:
            product_order.append(k)

    datasets: list[dict] = []
    for product_key in product_order:
        day_map = points_by_product.get(product_key)
        if not day_map:
            continue
        datasets.append(
            {
                "label": label_by_product.get(product_key, product_key),
                "data": [day_map.get(day) for day in labels],
            }
        )

    return {"labels": labels, "datasets": datasets}


def _parse_only_trading_days(request) -> bool:
    if "only_trading_days" not in request.GET:
        return True
    v = (request.GET.get("only_trading_days") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _parse_recent_trading_days(request) -> tuple[int | None, str]:
    raw = (request.GET.get("recent") or "").strip().lower()
    if raw in ("21", "63", "126", "252"):
        return int(raw), raw
    if raw == "all":
        return 0, "all"
    return None, raw


def _parse_nav_time_mode(request) -> str:
    raw = (request.GET.get("time_mode") or "").strip().lower()
    if raw == "custom":
        return "custom"
    return "recent"


def _parse_compare_recent_window(request) -> tuple[int, str]:
    raw = (request.GET.get("recent") or "").strip().lower()
    if raw in ("21", "63", "126", "252"):
        return int(raw), raw
    if raw == "all":
        return 0, "all"
    # 默认近 3 个月（约 63 个交易日）
    return 63, "63"


def _slice_compare_rows(rows: list[dict], recent_window: int) -> list[dict]:
    if recent_window <= 0:
        return list(rows)
    if len(rows) <= recent_window:
        return list(rows)
    return rows[-recent_window:]


def _filter_compare_rows_by_date_range(
    rows: list[dict], date_from: str | None, date_to: str | None
) -> list[dict]:
    df = (date_from or "").strip() or None
    dt = (date_to or "").strip() or None
    if not df and not dt:
        return list(rows)
    out: list[dict] = []
    for row in rows:
        day = str(row.get("report_date") or "")[:10]
        if not day:
            continue
        if df and day < df:
            continue
        if dt and day > dt:
            continue
        out.append(row)
    return out


def _slice_fund_nav_rows_by_recent_days(rows: list[dict], recent_window: int) -> list[dict]:
    if recent_window <= 0:
        return list(rows)
    days: list[str] = []
    seen_days: set[str] = set()
    for doc in rows:
        nd = doc.get("nav_date")
        if hasattr(nd, "strftime"):
            day = nd.strftime("%Y-%m-%d")
        else:
            day = str(nd or "")[:10]
        if not day or day in seen_days:
            continue
        seen_days.add(day)
        days.append(day)
    days_sorted = sorted(days)
    if len(days_sorted) <= recent_window:
        return list(rows)
    keep_days = set(days_sorted[-recent_window:])
    out: list[dict] = []
    for doc in rows:
        nd = doc.get("nav_date")
        if hasattr(nd, "strftime"):
            day = nd.strftime("%Y-%m-%d")
        else:
            day = str(nd or "")[:10]
        if day in keep_days:
            out.append(doc)
    return out


def _slice_alpha_daily_rows_by_recent_days(rows: list[dict], recent_window: int) -> list[dict]:
    if recent_window <= 0:
        return list(rows)
    seen_days: set[str] = set()
    ordered_days: list[str] = []
    for doc in rows:
        rd = doc.get("report_date")
        if hasattr(rd, "strftime"):
            day = rd.strftime("%Y-%m-%d")
        else:
            day = str(rd or "")[:10]
        if not day or day in seen_days:
            continue
        seen_days.add(day)
        ordered_days.append(day)
    ordered_days = sorted(ordered_days)
    if len(ordered_days) <= recent_window:
        return list(rows)
    keep_days = set(ordered_days[-recent_window:])
    out: list[dict] = []
    for doc in rows:
        rd = doc.get("report_date")
        if hasattr(rd, "strftime"):
            day = rd.strftime("%Y-%m-%d")
        else:
            day = str(rd or "")[:10]
        if day in keep_days:
            out.append(doc)
    return out


def _rebase_compare_rows(rows: list[dict]) -> list[dict]:
    """将区间起点重置为 1：产品和基准都以窗口首日为基准。"""
    if not rows:
        return []
    p0 = rows[0].get("product_nav_norm")
    b0 = rows[0].get("bench_nav_norm")
    out: list[dict] = []
    for r in rows:
        x = dict(r)
        p = x.get("product_nav_norm")
        b = x.get("bench_nav_norm")
        x["product_nav_norm"] = (float(p) / float(p0)) if (p is not None and p0 not in (None, 0)) else None
        x["bench_nav_norm"] = (float(b) / float(b0)) if (b is not None and b0 not in (None, 0)) else None
        pn = x.get("product_nav_norm")
        bn = x.get("bench_nav_norm")
        x["excess_cum"] = (pn / bn - 1.0) if (pn is not None and bn not in (None, 0)) else None
        out.append(x)
    return out


def _compute_compare_metrics(
    rows: list[dict],
    *,
    risk_free_annual: float,
    annualization_factor: int,
    min_sample_days: int,
    mdd_zero_as_na: bool,
) -> dict[str, float | None]:
    if not rows:
        return {"max_drawdown": None, "annual_vol": None, "sharpe": None}

    navs: list[float] = []
    rets: list[float] = []
    for r in rows:
        nav = r.get("product_nav")
        if nav is not None:
            navs.append(float(nav))
        pr = r.get("product_ret")
        if pr is not None:
            rets.append(float(pr))
    if not navs:
        return {"max_drawdown": None, "annual_vol": None, "sharpe": None}

    peak = navs[0]
    mdd = 0.0
    for x in navs:
        if x > peak:
            peak = x
        if peak > 0:
            dd = x / peak - 1.0
            if dd < mdd:
                mdd = dd

    mdd_out: float | None = None if (mdd_zero_as_na and abs(mdd) < 1e-12) else mdd
    if len(rets) < max(2, min_sample_days):
        return {"max_drawdown": mdd_out, "annual_vol": None, "sharpe": None}

    mean_ret = sum(rets) / len(rets)
    var = sum((x - mean_ret) ** 2 for x in rets) / (len(rets) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    annual_vol = std * math.sqrt(annualization_factor)

    rf_daily = (1.0 + max(-0.9999, float(risk_free_annual))) ** (
        1.0 / annualization_factor
    ) - 1.0
    excess = [x - rf_daily for x in rets]
    mean_ex = sum(excess) / len(excess)
    std_ex_var = sum((x - mean_ex) ** 2 for x in excess) / (len(excess) - 1)
    std_ex = math.sqrt(std_ex_var) if std_ex_var > 0 else 0.0
    sharpe = (mean_ex / std_ex * math.sqrt(annualization_factor)) if std_ex > 0 else None
    return {"max_drawdown": mdd_out, "annual_vol": annual_vol, "sharpe": sharpe}


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
        recent_n = 21
        recent_raw = "21"

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
    """基金净值页：展示 fund_nav_real 下博士一号与泽鑫多维净值表数据。"""
    try:
        limit = int(request.GET.get("limit") or 200)
    except ValueError:
        limit = 200
    limit = max(1, min(limit, 10000))

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    recent_window, recent_raw = _parse_compare_recent_window(request)
    use_recent_mode = recent_raw in ("21", "63", "126", "252", "all")
    effective_limit = 10000 if use_recent_mode else limit

    fund_nav_debug_enabled = getattr(settings, "FUND_NAV_PAGE_DEBUG", False)
    show_debug = fund_nav_debug_enabled and request.GET.get("debug") == "1"

    form_submitted = (request.GET.get("nav_q") or "").strip() == "1"
    if form_submitted:
        product_keys = fund_nav_product_keys_from_raw_nav_request(
            request.GET, form_submitted=True
        )
    else:
        # 首次进入：默认博士一号主份额
        product_keys = [settings.NAV_REAL_WZ_BSYH_MASTER]

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
                limit=effective_limit,
                date_from=date_from or None,
                date_to=date_to or None,
                product_keys=product_keys,
            )
            rows = _slice_fund_nav_rows_by_recent_days(rows, recent_window)
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
    fund_nav_header_pairs = list(zip(headers_zh, visible_field_keys))
    fund_nav_body_rows = [list(zip(r, visible_field_keys)) for r in table_rows]

    debug_info_text: str | None = None
    if show_debug and error_msg is None and elapsed_ms is not None:
        dbg = {
            "mongo_query_per_collection": build_fund_nav_mongo_query(
                date_from or None, date_to or None
            ),
            "product_keys": product_keys,
            "limit": effective_limit,
            "elapsed_ms": round(elapsed_ms, 3),
            "row_count": len(rows),
        }
        debug_info_text = json.dumps(dbg, ensure_ascii=False, indent=2, default=str)

    chart_data = _build_fund_nav_chart_data(rows, product_keys)

    context = {
        "headers_zh": headers_zh,
        "column_catalog": column_catalog,
        "table_rows": table_rows,
        "fund_nav_header_pairs": fund_nav_header_pairs,
        "fund_nav_body_rows": fund_nav_body_rows,
        "fund_nav_left_align_en": FUND_NAV_TABLE_LEFT_ALIGN_EN,
        "raw_count": len(rows),
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
        "recent": recent_raw,
        "error_msg": error_msg,
        "fund_nav_products": FUND_NAV_PRODUCTS,
        "fund_nav_sidebar_products": fund_nav_portal_sidebar_products(),
        "fund_nav_selection": fund_nav_selection,
        "fund_nav_empty_product_pick": fund_nav_empty_product_pick,
        "fund_nav_debug_enabled": fund_nav_debug_enabled,
        "show_debug": show_debug,
        "debug_info_text": debug_info_text,
        "fund_nav_mongo_db": settings.MONGODB_FUND_NAV_REAL_DB,
        "fund_nav_coll_master": settings.NAV_REAL_WZ_BSYH_MASTER,
        "fund_nav_coll_b": settings.NAV_REAL_WZ_BSYH_B,
        "fund_nav_zxdw_collections": getattr(
            settings, "MONGODB_ZXDW_NAV_COLLECTIONS", ()
        ),
        "chart_json": json.dumps(chart_data, ensure_ascii=False),
    }
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if is_ajax:
        return JsonResponse(
            {
                "ok": error_msg is None,
                "error_msg": error_msg,
                "raw_count": len(rows),
                "headers_zh": headers_zh,
                "table_rows": table_rows,
                "fund_nav_column_en_keys": visible_field_keys,
                "fund_nav_empty_product_pick": fund_nav_empty_product_pick,
                "debug_info_text": debug_info_text,
                "chart_data": chart_data,
                "recent": recent_raw,
            },
            json_dumps_params={"ensure_ascii": False},
        )
    return render(request, "portal/raw_nav.html", context)


@login_required(login_url="/")
def nav_bench_compare(request):
    """产品净值 vs 指数基准对比页（计算结果自动落库到 basic_rq.calc_*）。"""
    mode = (request.GET.get("mode") or "").strip().lower()
    try:
        limit = int(request.GET.get("limit") or 200)
    except ValueError:
        limit = 200
    limit = max(1, min(limit, 1000))
    date_from = (request.GET.get("date_from") or "").strip() or None
    date_to = (request.GET.get("date_to") or "").strip() or None
    date_error_msg: str | None = None
    try:
        if date_from:
            datetime.strptime(date_from, "%Y-%m-%d")
        if date_to:
            datetime.strptime(date_to, "%Y-%m-%d")
        if date_from and date_to and date_from > date_to:
            raise ValueError("开始日期不能晚于结束日期。")
    except ValueError:
        date_error_msg = "日期格式须为 YYYY-MM-DD（请使用下方日期选择器）。"

    products = distinct_product_names()
    selected = (request.GET.get("product_name") or "").strip()
    if not selected and products:
        selected = products[0]
    _recent_window, recent_raw = _parse_compare_recent_window(request)
    use_custom_query = mode == "custom"

    error_msg = date_error_msg
    rows_all: list[dict] = []
    rows: list[dict] = []
    bench_code = ""
    bench_name = ""
    elapsed_ms: float | None = None
    from_cache = False
    if not products:
        error_msg = "库中暂无产品数据，请先导入 Alpha 日报。"
    elif not selected:
        error_msg = "请选择产品。"
    elif date_error_msg is None:
        try:
            if selected not in products:
                raise ValueError("所选产品无效，请重新选择。")
            t0 = time.perf_counter()
            result = build_and_store_nav_bench_compare(product_name=selected)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            rows_all = result.get("rows") or []
            bench_code = str(result.get("bench_code") or "")
            bench_name = str(result.get("bench_name") or bench_code or "")
            from_cache = bool(result.get("from_cache"))
            if use_custom_query:
                rows = _filter_compare_rows_by_date_range(rows_all, date_from, date_to)
                if len(rows) > limit:
                    rows = rows[-limit:]
            else:
                rows = _slice_compare_rows(rows_all, _recent_window)
            rows = _rebase_compare_rows(rows)
        except ValueError as exc:
            error_msg = str(exc)
        except Exception as exc:
            error_msg = str(exc)

    labels = [r.get("report_date") for r in rows if r.get("report_date")]
    product_data = [r.get("product_nav_norm") for r in rows]
    bench_data = [r.get("bench_nav_norm") for r in rows]
    chart_json = json.dumps(
        {
            "labels": labels,
            "datasets": [
                {"label": f"{selected} 归一化净值", "data": product_data},
                {"label": f"{bench_name or bench_code} 归一化基准", "data": bench_data},
            ],
        },
        ensure_ascii=False,
    )
    risk_free_annual = float(getattr(settings, "NAV_COMPARE_RISK_FREE_ANNUAL", 0.0))
    annualization_factor = int(getattr(settings, "NAV_ANNUALIZATION_FACTOR", 252))
    if annualization_factor <= 0:
        annualization_factor = 252
    min_sample_days = int(getattr(settings, "NAV_MIN_SAMPLE_DAYS", 60))
    if min_sample_days <= 0:
        min_sample_days = 2
    mdd_zero_as_na = bool(getattr(settings, "NAV_MDD_ZERO_AS_NA", False))
    metrics = _compute_compare_metrics(
        rows,
        risk_free_annual=risk_free_annual,
        annualization_factor=annualization_factor,
        min_sample_days=min_sample_days,
        mdd_zero_as_na=mdd_zero_as_na,
    )
    metrics_display = {
        "max_drawdown_pct": (metrics["max_drawdown"] * 100.0) if metrics["max_drawdown"] is not None else None,
        "annual_vol_pct": (metrics["annual_vol"] * 100.0) if metrics["annual_vol"] is not None else None,
        "sharpe": metrics["sharpe"],
    }
    alpha_rows_raw: list[dict] = []
    alpha_headers_zh = [cn for cn, _en in ALPHA_DAILY_COLUMNS]
    alpha_field_keys = [en for _cn, en in ALPHA_DAILY_COLUMNS]
    alpha_table_rows: list[list[str]] = []
    if selected:
        try:
            if use_custom_query:
                alpha_rows_raw = fetch_alpha_daily_documents(
                    limit=limit,
                    date_from=date_from,
                    date_to=date_to,
                    product_name=selected,
                )
            else:
                alpha_rows_raw = fetch_alpha_daily_documents(limit=1000, product_name=selected)
                alpha_rows_raw = _slice_alpha_daily_rows_by_recent_days(alpha_rows_raw, _recent_window)
            alpha_table_rows = [row_to_display_cells(doc, alpha_field_keys) for doc in alpha_rows_raw]
        except Exception:
            alpha_rows_raw = []
            alpha_table_rows = []
    alpha_header_pairs = list(zip(alpha_headers_zh, alpha_field_keys))
    alpha_body_rows = [list(zip(r, alpha_field_keys)) for r in alpha_table_rows]

    context = {
        "error_msg": error_msg,
        "products": products,
        "product_groups": _group_compare_products(products, selected),
        "selected_product": selected,
        "rows": rows,
        "row_count": len(rows),
        "bench_code": bench_code,
        "bench_name": bench_name or bench_code,
        "from_cache": from_cache,
        "recent": recent_raw,
        "metrics": metrics_display,
        "chart_json": chart_json,
        "elapsed_ms": round(elapsed_ms, 3) if elapsed_ms is not None else None,
        "calc_daily_collection": settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_DAILY,
        "calc_summary_collection": settings.MONGODB_RQ_BENCH_CALC_NAV_BENCH_SUMMARY,
        "annualization_factor": annualization_factor,
        "min_sample_days": min_sample_days,
        "risk_free_annual_pct": risk_free_annual * 100.0,
        "limit": limit,
        "date_from": date_from or "",
        "date_to": date_to or "",
        "mode": mode,
        "alpha_headers_zh": alpha_headers_zh,
        "alpha_table_rows": alpha_table_rows,
        "alpha_header_pairs": alpha_header_pairs,
        "alpha_body_rows": alpha_body_rows,
        "alpha_left_align_en": ALPHA_COMPARE_TABLE_LEFT_ALIGN_EN,
        "alpha_row_count": len(alpha_rows_raw),
    }
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if is_ajax:
        return JsonResponse(
            {
                "ok": error_msg is None,
                "error_msg": error_msg,
                "selected_product": selected,
                "bench_name": bench_name or bench_code,
                "row_count": len(rows),
                "recent": recent_raw,
                "limit": limit,
                "date_from": date_from or "",
                "date_to": date_to or "",
                "mode": mode,
                "metrics": metrics_display,
                "chart_data": {
                    "labels": labels,
                    "datasets": [
                        {"label": f"{selected} 归一化净值", "data": product_data},
                        {"label": f"{bench_name or bench_code} 归一化基准", "data": bench_data},
                    ],
                },
                "alpha_headers_zh": alpha_headers_zh,
                "alpha_table_rows": alpha_table_rows,
                "alpha_column_en_keys": alpha_field_keys,
                "alpha_row_count": len(alpha_rows_raw),
            },
            json_dumps_params={"ensure_ascii": False},
        )
    return render(request, "portal/nav_bench_compare.html", context)


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

    if recent_raw in ("21", "63", "126", "252"):
        use_recent = int(recent_raw)
    elif recent_raw == "all":
        use_recent = 0
    elif date_from or date_to:
        use_recent = None
    else:
        use_recent = 21

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

