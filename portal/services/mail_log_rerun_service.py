"""从 MAIL_LOGS 单条记录重跑管理命令并回写同一文档。"""
from __future__ import annotations

import io
import traceback
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from django.core.management import call_command
from django.utils import timezone

from portal.db.mongo import get_mail_logs_collection
from portal.scheduler.alpha_mail_schedule import DEFAULT_MAIL_SCHEDULER_SCHEDULES
from portal.scheduler.alpha_mail_scheduler import (
    _infer_scheduled_job_outcome,
    _kwargs_bson_safe,
    _merge_outcome_with_notify_snapshot,
    _notify_snapshot_for_mongo,
    _resolve_mail_log_target_fields,
    _scheduler_mail_log_max_chars,
    _truncate_log_text,
)
from portal.services.mail_import_common import strip_mail_job_result_json

_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    cmd for _, cmd, _ in DEFAULT_MAIL_SCHEDULER_SCHEDULES
)

# 未在 add_arguments 中声明 --force 的命令，call_command 不能传 force=（会报 Unknown option force）
_COMMANDS_WITHOUT_FORCE_FLAG: frozenset[str] = frozenset(
    {
        "auto_import_cjqh_settle_mail",
        "auto_import_wkqh_settle_mail",
        "auto_import_htqh_settle_mail",
        "auto_import_ghzq_settle_mail",
        "auto_import_qichat_t0_mail",
        "sync_t0_performance",
        "sync_position_close_record",
        "purge_position_daily_cache",
    }
)


def _compact_target_ymd(target_date: Any) -> str | None:
    if target_date is None:
        return None
    s = str(target_date).strip()
    if not s:
        return None
    d = s.replace("-", "")[:8]
    if len(d) == 8 and d.isdigit():
        return d
    return None


def _iso_yyyy_mm_dd(ymd8: str) -> str:
    return f"{ymd8[:4]}-{ymd8[4:6]}-{ymd8[6:8]}"


def _date_kwargs_for_command(command_name: str, ymd8: str | None) -> dict[str, Any]:
    """根据日志中的 target_date（YYYYMMDD）构造传给 call_command 的日期类参数（不含 force）。"""
    if not ymd8:
        return {}
    iso = _iso_yyyy_mm_dd(ymd8)
    if command_name in (
        "auto_import_lhjx_position_mail",
        "auto_import_wzsl_position_mail",
        "auto_import_alpha_target_position_mail",
        "auto_import_alpha_source_position_mail",
    ):
        return {"position_date": iso}
    if command_name == "update_rq_bench":
        return {"trade_day": iso}
    if command_name == "auto_import_htzq_ht1_capital_mail":
        return {"statement_date": iso}
    if command_name == "auto_import_zxdw_nav_mail":
        return {"report_date": iso}
    if command_name == "auto_import_alpha_mail":
        return {"subject_date": ymd8}
    if command_name == "auto_import_qichat_t0_mail":
        return {"ref_date": iso}
    if command_name.endswith("_settle_mail"):
        return {"subject_date": ymd8}
    if command_name.endswith("_nav_mail"):
        return {"nav_date": iso}
    if command_name in ("sync_t0_performance", "sync_position_close_record", "purge_position_daily_cache"):
        return {"trade_date": iso} if command_name != "sync_t0_performance" else {}
    return {}


def _requires_target_date(command_name: str) -> bool:
    if command_name in ("sync_t0_performance", "sync_position_close_record", "purge_position_daily_cache"):
        return command_name != "sync_t0_performance"
    return bool(_date_kwargs_for_command(command_name, "20000101"))


def rerun_and_update_mail_log(log_id: str) -> tuple[bool, str]:
    """
    按日志中的 command_name、target_date（及 scheduler_job_key 写回 kwargs）重跑命令，
    将 stdout/stderr、推断结果等更新到同一 Mongo 文档。
    对声明了 --force 的命令会传 force=True，以便跳过交易日等限制；结算单 / qichat / sync_t0 等不传。
    """
    try:
        oid = ObjectId((log_id or "").strip())
    except InvalidId:
        return False, "无效的日志 ID。"

    coll = get_mail_logs_collection()
    doc = coll.find_one({"_id": oid})
    if not doc:
        return False, "未找到该条日志。"

    command_name = str(doc.get("command_name") or "").strip()
    if not command_name:
        return False, "日志缺少 command_name。"
    if command_name not in _ALLOWED_COMMANDS:
        return False, f"不允许从页面重跑的命令: {command_name}"

    ymd8 = _compact_target_ymd(doc.get("target_date"))
    if _requires_target_date(command_name) and not ymd8:
        return False, "该日志缺少有效的 target_date（YYYYMMDD），无法按原业务日重跑。"

    date_kw = _date_kwargs_for_command(command_name, ymd8)
    call_kw: dict[str, Any] = dict(date_kw)
    use_force = command_name not in _COMMANDS_WITHOUT_FORCE_FLAG
    if use_force:
        call_kw["force"] = True

    started_at = timezone.now()
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    exc: BaseException | None = None
    try:
        call_command(command_name, stdout=out_buf, stderr=err_buf, **call_kw)
    except BaseException as run_exc:
        exc = run_exc

    finished_at = timezone.now()
    out_text_raw = out_buf.getvalue()
    out_text, notify_snapshot = strip_mail_job_result_json(out_text_raw)
    err_text = err_buf.getvalue()
    target_subject, target_date = _resolve_mail_log_target_fields(
        out_text, notify_snapshot
    )
    if not target_subject:
        target_subject = doc.get("target_subject")
    if not target_date:
        target_date = doc.get("target_date")

    if exc is not None:
        outcome = "failure"
        import_ok = False
        soft_reason = str(exc)
    else:
        outcome, soft_reason = _infer_scheduled_job_outcome(
            command_name, out_text, err_text
        )
        outcome, import_ok, soft_reason = _merge_outcome_with_notify_snapshot(
            outcome, soft_reason, notify_snapshot
        )

    max_len = _scheduler_mail_log_max_chars()
    out_text = _truncate_log_text(out_text or "", max_len)
    err_text = _truncate_log_text(err_text or "", max_len)

    ek: dict[str, Any] = {}
    sk = doc.get("scheduler_job_key")
    if sk is not None and str(sk).strip():
        ek["scheduler_job_key"] = sk
    ek.update(date_kw)
    kwargs_log = _kwargs_bson_safe(ek, force=use_force)

    set_doc: dict[str, Any] = {
        "log_type": outcome,
        "import_succeeded": import_ok,
        "started_at": started_at,
        "finished_at": finished_at,
        "stdout": out_text,
        "stderr": err_text,
        "kwargs": kwargs_log,
        "target_subject": target_subject,
        "target_date": target_date,
        "failure_reason": None if import_ok else soft_reason,
        "rerun_from_portal_at": finished_at,
    }
    ns = _notify_snapshot_for_mongo(notify_snapshot)
    set_doc["notify_snapshot"] = ns

    unset_doc: dict[str, str] = {}
    if exc is not None:
        tb = traceback.format_exc()
        tb = _truncate_log_text(tb, max_len)
        set_doc["error_type"] = type(exc).__name__
        set_doc["error_message"] = str(exc)
        set_doc["traceback"] = tb
    elif outcome == "failure" and not import_ok:
        set_doc["error_type"] = "ImportOutcome"
        set_doc["error_message"] = soft_reason or ""
        set_doc["traceback"] = ""
    else:
        unset_doc["error_type"] = ""
        unset_doc["error_message"] = ""
        unset_doc["traceback"] = ""

    # call_command 内部分管理命令会 close_mongo_client()，须用新连接写回日志
    coll = get_mail_logs_collection()
    if unset_doc:
        coll.update_one({"_id": oid}, {"$set": set_doc, "$unset": unset_doc})
    else:
        coll.update_one({"_id": oid}, {"$set": set_doc})

    if exc is not None:
        return False, f"命令已执行但抛出异常: {exc}"
    if not import_ok:
        return True, "已重跑并更新日志（仍未标记为导入成功，请查看 failure_reason / stdout）。"
    return True, "已重跑并更新日志（import_succeeded=true）。"
