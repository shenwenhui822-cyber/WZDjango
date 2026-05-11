"""T0 表现页面：周度绩效 t0_order + 日内明细 performance，FTP/CSV 同步。"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings as dj_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from portal.t0_performance.qichat_import import (
    QichatImportResult,
    distinct_t0_order_product_names,
    fetch_t0_order_rows,
    import_qichat_csv_dir,
)
from portal.t0_performance.sync_service import (
    distinct_performance_account_names,
    fetch_performance_rows,
    sync_t0_from_ftp,
)


def _flatten_errs(errors: list[str]) -> str:
    if not errors:
        return ""
    return "；".join(errors[:5]) + ("…" if len(errors) > 5 else "")

T0_TEAM_HUIZHU = "huizhu"  # 汇祝 — 日内交易汇总
T0_TEAM_SHENDU = "shendu"  # 深度秩序 — 周度绩效
T0_TEAM_IDS = (T0_TEAM_SHENDU, T0_TEAM_HUIZHU)
T0_TEAM_LABELS = {
    T0_TEAM_HUIZHU: "汇祝团队",
    T0_TEAM_SHENDU: "深度秩序团队",
}


def _redirect_t0_preserving_filters(
    weekly_product: str,
    intraday_account: str,
    t0_team: str,
) -> HttpResponseRedirect:
    q: dict[str, str] = {}
    wp = weekly_product.strip()
    ia = intraday_account.strip()
    tt = t0_team.strip()
    if wp:
        q["weekly_product"] = wp
    if ia:
        q["intraday_account"] = ia
    if tt and tt in T0_TEAM_IDS:
        q["t0_team"] = tt
    url = reverse("portal:alpha_t0")
    if q:
        url = f"{url}?{urlencode(q)}"
    return HttpResponseRedirect(url)


@login_required(login_url="/")
def t0_performance(request):
    weekly_product_sel = (
        request.GET.get("weekly_product")
        or request.POST.get("weekly_product")
        or ""
    ).strip()
    intraday_account_sel = (
        request.GET.get("intraday_account")
        or request.POST.get("intraday_account")
        or ""
    ).strip()
    t0_team_sel = (
        request.GET.get("t0_team") or request.POST.get("t0_team") or T0_TEAM_HUIZHU
    ).strip()
    if t0_team_sel not in T0_TEAM_IDS:
        t0_team_sel = T0_TEAM_HUIZHU

    weekly_filter_q = weekly_product_sel or None
    intraday_filter_q = intraday_account_sel or None

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "sync_ftp":
            sync_res = sync_t0_from_ftp()
            if sync_res.errors:
                ee = _flatten_errs(sync_res.errors)
                if sync_res.rows_upserted or sync_res.files_processed:
                    messages.warning(
                        request,
                        f"[FTP 日内]部分完成：{sync_res.files_processed} 个文件，写入 {sync_res.rows_upserted} 行。"
                        + (f" 问题：{ee}" if ee else ""),
                    )
                else:
                    messages.error(
                        request,
                        "[FTP 日内]未完成。" + (ee if ee else ""),
                    )
            else:
                messages.success(
                    request,
                    f"[FTP 日内]完成：{sync_res.files_processed} 个文件，{sync_res.rows_upserted} 行。",
                )
        elif action == "sync_qichat":
            r: QichatImportResult = import_qichat_csv_dir(
                Path(dj_settings.T0_QICHAT_IMPORT_DIR)
            )
            if r.errors:
                ee = _flatten_errs(r.errors)
                if r.rows_upserted:
                    messages.warning(
                        request,
                        f"[周度CSV]写入 {r.rows_upserted} 行，{r.files_processed} 个文件。"
                        + (f" 问题：{ee}" if ee else ""),
                    )
                else:
                    messages.error(
                        request,
                        "[周度CSV]未完成。" + (ee if ee else ""),
                    )
            else:
                messages.success(
                    request,
                    f"[周度CSV]完成：{r.files_processed} 个文件，{r.rows_upserted} 行。",
                )
        else:
            messages.warning(request, "未知操作")

        return _redirect_t0_preserving_filters(
            weekly_product_sel, intraday_account_sel, t0_team_sel
        )

    order_rows: list = []
    perf_rows: list = []
    if t0_team_sel == T0_TEAM_SHENDU:
        order_rows = fetch_t0_order_rows(
            limit=5000,
            product_name_filter=weekly_filter_q,
        )
    if t0_team_sel == T0_TEAM_HUIZHU:
        perf_rows = fetch_performance_rows(
            limit=5000,
            account_name_filter=intraday_filter_q,
        )

    product_options = (
        distinct_t0_order_product_names() if t0_team_sel == T0_TEAM_SHENDU else []
    )
    account_options = (
        distinct_performance_account_names() if t0_team_sel == T0_TEAM_HUIZHU else []
    )

    q_common: dict[str, str] = {}
    if weekly_product_sel:
        q_common["weekly_product"] = weekly_product_sel
    if intraday_account_sel:
        q_common["intraday_account"] = intraday_account_sel
    q_shendu = {**q_common, "t0_team": T0_TEAM_SHENDU}
    q_huizhu = {**q_common, "t0_team": T0_TEAM_HUIZHU}
    url_base = reverse("portal:alpha_t0")
    t0_url_shendu = f"{url_base}?{urlencode(q_shendu)}"
    t0_url_huizhu = f"{url_base}?{urlencode(q_huizhu)}"

    ctx = {
        "page_title": "T0表现",
        "breadcrumb_label": "T0表现",
        "t0_team": t0_team_sel,
        "t0_team_labels": T0_TEAM_LABELS,
        "t0_url_shendu": t0_url_shendu,
        "t0_url_huizhu": t0_url_huizhu,
        "show_weekly": t0_team_sel == T0_TEAM_SHENDU,
        "show_intraday": t0_team_sel == T0_TEAM_HUIZHU,
        "order_rows": order_rows,
        "order_row_count": len(order_rows),
        "performance_rows": perf_rows,
        "performance_row_count": len(perf_rows),
        "weekly_product": weekly_product_sel,
        "intraday_account": intraday_account_sel,
        "weekly_product_options": product_options,
        "intraday_account_options": account_options,
        "T0_FTP_REMOTE_DIR": dj_settings.T0_FTP_REMOTE_DIR,
        "T0_FTP_XLSX_SUFFIX": getattr(
            dj_settings, "T0_FTP_XLSX_SUFFIX", "_wuzhi_日内交易汇总.xlsx"
        ),
        "MONGODB_T0_PERFORMANCE_DB": dj_settings.MONGODB_T0_PERFORMANCE_DB,
        "MONGODB_T0_ORDER_COLLECTION": dj_settings.MONGODB_T0_ORDER_COLLECTION,
        "MONGODB_T0_PERFORMANCE_COLLECTION": dj_settings.MONGODB_T0_PERFORMANCE_COLLECTION,
        "T0_QICHAT_IMPORT_DIR": dj_settings.T0_QICHAT_IMPORT_DIR,
    }
    return render(request, "portal/t0_performance.html", ctx)
