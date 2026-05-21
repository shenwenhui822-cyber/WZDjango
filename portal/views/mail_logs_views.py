"""MAIL_LOGS 定时任务运行记录列表页。"""

from __future__ import annotations

import datetime as std_datetime
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone as dj_timezone
from django.views.decorators.http import require_POST

from portal.db.mongo import get_mail_logs_collection
from portal.services.mail_log_rerun_service import rerun_and_update_mail_log


def _fmt_cell(val: Any) -> str:
    if val is None:
        return "—"
    if hasattr(val, "strftime"):
        try:
            return val.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(val)
    return str(val)


def _fmt_finished_at_shanghai(val: Any) -> str:
    """
    将 finished_at 格式化为上海时间。
    MAIL_LOGS 中多为 UTC 写入后由 PyMongo 读出的 naive datetime，或 Unix 时间戳。
    """
    if val is None:
        return "—"
    try:
        if isinstance(val, (int, float)):
            ts = float(val)
            if ts > 1e12:
                ts /= 1000.0
            dt = std_datetime.datetime.fromtimestamp(
                ts, tz=std_datetime.timezone.utc
            )
        elif hasattr(val, "strftime"):
            dt = val
            if dj_timezone.is_naive(dt):
                dt = dj_timezone.make_aware(dt, std_datetime.timezone.utc)
        else:
            return str(val)
        local = dj_timezone.localtime(dt)
        return local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(val)


def _resolve_log_target_fields(doc: dict[str, Any]) -> tuple[str, str]:
    """列表展示：优先顶层字段，旧日志从 notify_snapshot 回填。"""
    snap = doc.get("notify_snapshot")
    if not isinstance(snap, dict):
        snap = None

    target_subject = doc.get("target_subject")
    if (target_subject is None or str(target_subject).strip() == "") and snap:
        for key in ("target_subject", "matched_subject"):
            raw = snap.get(key)
            if raw is not None and str(raw).strip():
                target_subject = raw
                break

    target_date = doc.get("target_date")
    if (target_date is None or str(target_date).strip() == "") and snap:
        for key in (
            "target_date",
            "nav_date",
            "report_date",
            "ymd",
            "target_trade_day",
            "subject_date",
            "statement_date",
            "position_date",
        ):
            raw = snap.get(key)
            if raw is None or str(raw).strip() == "":
                continue
            s = str(raw).strip().replace("-", "")[:8]
            if len(s) == 8 and s.isdigit():
                target_date = s
                break

    return _fmt_cell(target_subject), _fmt_cell(target_date)


def _import_succeeded_filter(request) -> tuple[dict[str, Any] | None, str]:
    """返回 (Mongo 查询片段, 当前筛选标签)。无片段表示不按该字段过滤。"""
    raw = (request.GET.get("import_succeeded") or "false").strip().lower()
    if raw in ("all", "any", "*"):
        return None, "all"
    if raw in ("true", "1", "yes", "ok"):
        return {"import_succeeded": True}, "true"
    return {"import_succeeded": False}, "false"


@login_required(login_url="/")
def mail_scheduler_logs(request):
    extra_q, mode = _import_succeeded_filter(request)
    try:
        lim = int((request.GET.get("limit") or "500").strip())
    except ValueError:
        lim = 500
    lim = max(50, min(lim, 2000))

    rows: list[dict[str, Any]] = []
    error: str | None = None
    try:
        coll = get_mail_logs_collection()
        query: dict[str, Any] = dict(extra_q) if extra_q else {}
        cursor = (
            coll.find(
                query,
                projection={
                    "_id": 1,
                    "finished_at": 1,
                    "target_date": 1,
                    "log_type": 1,
                    "import_succeeded": 1,
                    "command_name": 1,
                    "target_subject": 1,
                    "failure_reason": 1,
                    "notify_snapshot": 1,
                },
            )
            .sort("finished_at", -1)
            .limit(lim)
        )
        for doc in cursor:
            ok = doc.get("import_succeeded")
            target_subject, target_date = _resolve_log_target_fields(doc)
            rows.append(
                {
                    "log_id": str(doc.get("_id")),
                    "finished_at": _fmt_finished_at_shanghai(doc.get("finished_at")),
                    "target_date": target_date,
                    "log_type": _fmt_cell(doc.get("log_type")),
                    "import_succeeded": ok if isinstance(ok, bool) else bool(ok),
                    "command_name": _fmt_cell(doc.get("command_name")),
                    "target_subject": target_subject,
                    "failure_reason": _fmt_cell(doc.get("failure_reason")),
                }
            )
    except Exception as exc:
        error = str(exc)

    return render(
        request,
        "portal/mail_logs.html",
        {
            "rows": rows,
            "error": error,
            "filter_mode": mode,
            "limit": lim,
        },
    )


@login_required(login_url="/")
@require_POST
def mail_log_rerun(request):
    log_id = (request.POST.get("log_id") or "").strip()
    ok, msg = rerun_and_update_mail_log(log_id)
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    nxt = (request.POST.get("next") or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(reverse("portal:mail_logs"))
