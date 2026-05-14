"""MAIL_LOGS 定时任务运行记录列表页。"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from portal.db.mongo import get_mail_logs_collection


def _fmt_cell(val: Any) -> str:
    if val is None:
        return "—"
    if hasattr(val, "strftime"):
        try:
            return val.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(val)
    return str(val)


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
                    "target_date": 1,
                    "log_type": 1,
                    "import_succeeded": 1,
                    "command_name": 1,
                    "target_subject": 1,
                    "failure_reason": 1,
                },
            )
            .sort("finished_at", -1)
            .limit(lim)
        )
        for doc in cursor:
            ok = doc.get("import_succeeded")
            rows.append(
                {
                    "target_date": _fmt_cell(doc.get("target_date")),
                    "log_type": _fmt_cell(doc.get("log_type")),
                    "import_succeeded": ok if isinstance(ok, bool) else bool(ok),
                    "command_name": _fmt_cell(doc.get("command_name")),
                    "target_subject": _fmt_cell(doc.get("target_subject")),
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
