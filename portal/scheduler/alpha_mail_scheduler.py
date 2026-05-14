
from __future__ import annotations

import io
import os
import re
import threading
import time
import traceback
from datetime import datetime

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from portal.db.mongo import bson_safe_value, get_mail_logs_collection
from portal.services.mail_import_common import send_alpha_notify_result_email
from portal.services.trade_calendar_service import (
    is_first_trading_day_of_iso_week,
    is_trade_date_iso,
)

_mail_logs_index_lock = threading.Lock()
_mail_logs_indexes_ready = False


def _scheduler_mail_log_enabled() -> bool:
    return os.getenv("SCHEDULER_MAIL_LOG_TO_MONGO_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _scheduler_result_alpha_notify_enabled() -> bool:
    return os.getenv("SCHEDULER_RESULT_ALPHA_NOTIFY_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _scheduler_mail_log_max_chars() -> int:
    try:
        return max(10_000, int(getattr(settings, "SCHEDULER_MAIL_LOG_MAX_CHARS", 400_000)))
    except (TypeError, ValueError):
        return 400_000


def _scheduler_result_email_body_max_chars() -> int:
    try:
        return max(5_000, int(getattr(settings, "SCHEDULER_RESULT_EMAIL_BODY_MAX_CHARS", 100_000)))
    except (TypeError, ValueError):
        return 100_000


def _truncate_log_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    head = max_len // 2
    tail = max_len - head - 32
    return text[:head] + "\n...[中间已省略]...\n" + text[-tail:]


def _valid_calendar_ymd(ymd: str) -> bool:
    try:
        datetime.strptime(ymd, "%Y%m%d")
        return True
    except (ValueError, TypeError):
        return False


def _extract_mail_log_target_fields(stdout_text: str) -> tuple[str | None, str | None]:
    """从命令 stdout 解析「目标主题」「目标日期」，写入 MAIL_LOGS（目标日期统一为 YYYYMMDD 字符串）。"""
    text = stdout_text or ""
    target_subject: str | None = None
    m_sub = re.search(r"^\s*目标主题[:：]\s*(.+?)(?:\r?\n|$)", text, flags=re.MULTILINE)
    if m_sub:
        target_subject = (m_sub.group(1) or "").strip() or None

    target_date: str | None = None
    m_day = re.search(r"主题日\s+(\d{8})\b", text)
    if m_day and _valid_calendar_ymd(m_day.group(1)):
        target_date = m_day.group(1)
    if target_date is None:
        m_rep = re.search(r"报告日\D*(\d{8})\b", text)
        if m_rep and _valid_calendar_ymd(m_rep.group(1)):
            target_date = m_rep.group(1)
    if target_date is None and target_subject:
        m_iso = re.search(r"(\d{4}-\d{2}-\d{2})", target_subject)
        if m_iso:
            compact = m_iso.group(1).replace("-", "")
            if _valid_calendar_ymd(compact):
                target_date = compact
    if target_date is None and target_subject:
        m_tail = re.search(r"(\d{8})\s*$", target_subject)
        if m_tail and _valid_calendar_ymd(m_tail.group(1)):
            target_date = m_tail.group(1)
    if target_date is None and target_subject:
        cands = re.findall(r"(?<![0-9])(\d{8})(?![0-9])", target_subject)
        ok = [c for c in cands if _valid_calendar_ymd(c)]
        if ok:
            target_date = ok[-1]
    return target_subject, target_date


def _kwargs_bson_safe(extra_kwargs: dict | None, *, force: bool) -> dict[str, object]:
    merged = dict(extra_kwargs or {})
    if force:
        merged["force"] = True
    out: dict[str, object] = {}
    for k, v in merged.items():
        key = str(k)
        try:
            out[key] = bson_safe_value(v)
        except Exception:
            out[key] = repr(v)
    return out


def _ensure_mail_logs_indexes(coll) -> None:
    global _mail_logs_indexes_ready
    if _mail_logs_indexes_ready:
        return
    with _mail_logs_index_lock:
        if _mail_logs_indexes_ready:
            return
        try:
            import pymongo

            coll.create_index(
                [
                    ("log_type", pymongo.ASCENDING),
                    ("command_name", pymongo.ASCENDING),
                    ("finished_at", pymongo.DESCENDING),
                ],
                name="scheduler_mail_logs_by_cmd_time",
                background=True,
            )
        except Exception:
            pass
        _mail_logs_indexes_ready = True


def _persist_mail_scheduler_run(
    *,
    command_name: str,
    ok: bool,
    started_at,
    finished_at,
    stdout_text: str,
    stderr_text: str,
    extra_kwargs: dict | None,
    force: bool,
    exc: BaseException | None = None,
    target_subject: str | None = None,
    target_date: str | None = None,
) -> None:
    if not _scheduler_mail_log_enabled():
        return
    max_len = _scheduler_mail_log_max_chars()
    stdout_text = _truncate_log_text(stdout_text or "", max_len)
    stderr_text = _truncate_log_text(stderr_text or "", max_len)
    kwargs_log = _kwargs_bson_safe(extra_kwargs, force=force)
    try:
        coll = get_mail_logs_collection()
        _ensure_mail_logs_indexes(coll)
        if ok:
            coll.insert_one(
                {
                    "log_type": "success",
                    "command_name": command_name,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "kwargs": kwargs_log,
                    "target_subject": target_subject,
                    "target_date": target_date,
                }
            )
        else:
            tb = ""
            if exc is not None:
                tb = traceback.format_exc()
                tb = _truncate_log_text(tb, max_len)
            coll.insert_one(
                {
                    "log_type": "failure",
                    "command_name": command_name,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "kwargs": kwargs_log,
                    "target_subject": target_subject,
                    "target_date": target_date,
                    "error_type": type(exc).__name__ if exc else "Unknown",
                    "error_message": str(exc) if exc else "",
                    "traceback": tb,
                }
            )
    except Exception as log_exc:
        ts = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[alpha-scheduler] [{ts}] 写入 mail_logs 运行记录失败（不影响任务）: {log_exc}"
        )


def _send_scheduler_result_email(
    *,
    ok: bool,
    command_name: str,
    started_at,
    finished_at,
    stdout_text: str,
    stderr_text: str,
    exc: BaseException | None = None,
    target_subject: str | None = None,
    target_date: str | None = None,
) -> None:
    if not _scheduler_result_alpha_notify_enabled():
        return
    max_body = _scheduler_result_email_body_max_chars()
    st = timezone.localtime(started_at).strftime("%Y-%m-%d %H:%M:%S")
    et = timezone.localtime(finished_at).strftime("%Y-%m-%d %H:%M:%S")
    parts = [
        "alpha_mail_scheduler 定时任务汇总（完整日志见 mail_logs.MAIL_LOGS）",
        "",
        f"命令: {command_name}",
        f"结果: {'成功' if ok else '失败'}",
        f"开始(本地): {st}",
        f"结束(本地): {et}",
    ]
    if target_subject or target_date:
        parts.extend(
            [
                f"目标主题: {target_subject or '-'}",
                f"目标日期(YYYYMMDD): {target_date or '-'}",
            ]
        )
    parts.extend(
        [
        "",
        "--- stdout ---",
        stdout_text or "(空)",
        "",
        "--- stderr ---",
        stderr_text or "(空)",
        ]
    )
    if not ok and exc is not None:
        parts.extend(["", "--- exception ---", f"{type(exc).__name__}: {exc}"])
    body = _truncate_log_text("\n".join(parts), max_body)
    tag = "OK" if ok else "FAILED"
    mail_subject = (
        f"[alpha-scheduler][{tag}] {command_name} "
        f"{timezone.localtime(finished_at).strftime('%Y-%m-%d %H:%M')}"
    )

    def _log_out(s: str) -> None:
        ts = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[alpha-scheduler] [{ts}] {s}")

    def _log_warn(s: str) -> None:
        ts = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[alpha-scheduler] [{ts}] {s}")

    try:
        send_alpha_notify_result_email(
            mail_subject=mail_subject,
            body=body,
            log_stdout=_log_out,
            log_stderr_warn=_log_warn,
        )
    except Exception as notify_exc:
        ts = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[alpha-scheduler] [{ts}] 调度汇总邮件发送异常（不影响任务）: {notify_exc}"
        )


_scheduler_started = False

# (HH:MM, management command name, kwargs)
# 邮件类任务在各自命令内校验「查询日～运行日」闭区间交易日个数 ≤ MAIL_JOB_MAX_TRADING_DAY_SPAN（默认 3）。
# auto_import_qichat_t0_mail：IMAP 动态主题拉取上周 CSV + 入库（仅调度：每周首个交易日，见 _should_skip_scheduled_job）。
# 每次定时任务结束：stdout/stderr 写入 mail_logs.MAIL_LOGS（含结构化字段 target_subject / target_date，见 _extract_mail_log_target_fields）；成功/失败均发 ALPHA_NOTIFY_* 汇总邮件（可配）。
_DEFAULT_SCHEDULES: list[tuple[str, str, dict]] = [
    ("09:00", "auto_import_htzq_ht1_capital_mail", {}),
    ("09:31", "auto_import_fund_nav_mail", {}),
    ("09:33", "auto_import_ghzq_settle_mail", {}),
    ("17:20", "auto_import_slh_nav_mail", {}),
    ("11:35", "auto_import_dyyh_nav_mail", {}),
    ("09:30", "update_rq_bench", {}),
    ("12:00", "auto_import_zxdw_nav_mail", {}),
    ("12:10", "auto_import_dylx_nav_mail", {}),
    ("11:10", "auto_import_ysh_nav_mail", {}),
    ("11:30", "auto_import_llh_nav_mail", {}),
    ("11:40", "auto_import_jlh_nav_mail", {}),
    ("14:10", "auto_import_dyctayh_nav_mail", {}),
    ("14:30", "auto_import_ylh_nav_mail", {}),
    ("17:10", "auto_import_wz_lyh_nav_mail", {}),
    ("11:50", "auto_import_ctayh_nav_mail", {}),
    ("16:30", "sync_t0_performance", {}),
    ("21:00", "auto_import_alpha_mail", {}),  
    ("18:00", "auto_import_wkqh_settle_mail", {}),
    ("18:05", "auto_import_cjqh_settle_mail", {}),
    ("18:10", "auto_import_stz053_nav_mail", {}),
    ("19:00", "auto_import_htqh_settle_mail", {}),
    ("20:00", "auto_import_qichat_t0_mail", {}),
]


def _htzq_ht1_capital_mail_enabled() -> bool:
    return os.getenv("HTZQ_HT1_CAPITAL_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _fund_nav_enabled() -> bool:
    return os.getenv("FUND_NAV_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _rq_bench_enabled() -> bool:
    return os.getenv("RQ_BENCH_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _zxdw_nav_mail_enabled() -> bool:
    return os.getenv("ZXDW_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _wkqh_settle_mail_enabled() -> bool:
    return os.getenv("WKQH_SETTLE_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _cjqh_settle_mail_enabled() -> bool:
    return os.getenv("CJQH_SETTLE_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _ghzq_settle_mail_enabled() -> bool:
    return os.getenv("GHZQ_SETTLE_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _htqh_settle_mail_enabled() -> bool:
    return os.getenv("HTQH_SETTLE_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _stz053_nav_mail_enabled() -> bool:
    return os.getenv("STZ053_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _slh_nav_mail_enabled() -> bool:
    return os.getenv("SLH_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _dyyh_nav_mail_enabled() -> bool:
    return os.getenv("DYYH_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _dylx_nav_mail_enabled() -> bool:
    return os.getenv("DYLX_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _ysh_nav_mail_enabled() -> bool:
    return os.getenv("YSH_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _llh_nav_mail_enabled() -> bool:
    return os.getenv("LLH_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _jlh_nav_mail_enabled() -> bool:
    return os.getenv("JLH_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _ylh_nav_mail_enabled() -> bool:
    return os.getenv("YLH_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _wz_lyh_nav_mail_enabled() -> bool:
    return os.getenv("WZ_LYH_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _ctayh_nav_mail_enabled() -> bool:
    return os.getenv("CTAYH_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _dyctayh_nav_mail_enabled() -> bool:
    return os.getenv("DYCTAYH_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _t0_ftp_sync_enabled() -> bool:
    return os.getenv("T0_FTP_SYNC_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _t0_qichat_weekly_sync_enabled() -> bool:
    return os.getenv("T0_QICHAT_WEEKLY_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _schedules() -> list[tuple[str, str, dict]]:
    s = list(_DEFAULT_SCHEDULES)
    if not _htzq_ht1_capital_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_htzq_ht1_capital_mail"]
    if not _fund_nav_enabled():
        s = [x for x in s if x[1] != "auto_import_fund_nav_mail"]
    if not _rq_bench_enabled():
        s = [x for x in s if x[1] != "update_rq_bench"]
    if not _zxdw_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_zxdw_nav_mail"]
    if not _wkqh_settle_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_wkqh_settle_mail"]
    if not _cjqh_settle_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_cjqh_settle_mail"]
    if not _ghzq_settle_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_ghzq_settle_mail"]
    if not _htqh_settle_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_htqh_settle_mail"]
    if not _stz053_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_stz053_nav_mail"]
    if not _slh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_slh_nav_mail"]
    if not _dyyh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_dyyh_nav_mail"]
    if not _dylx_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_dylx_nav_mail"]
    if not _ysh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_ysh_nav_mail"]
    if not _llh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_llh_nav_mail"]
    if not _jlh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_jlh_nav_mail"]
    if not _ctayh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_ctayh_nav_mail"]
    if not _dyctayh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_dyctayh_nav_mail"]
    if not _ylh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_ylh_nav_mail"]
    if not _wz_lyh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_wz_lyh_nav_mail"]
    if not _t0_ftp_sync_enabled():
        s = [x for x in s if x[1] != "sync_t0_performance"]
    if not _t0_qichat_weekly_sync_enabled():
        s = [x for x in s if x[1] != "auto_import_qichat_t0_mail"]
    return s


def _run_job(command_name: str, *, force: bool, extra_kwargs: dict | None = None) -> None:
    ts = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[alpha-scheduler] [{ts}] 触发执行 {command_name} ...")
    kwargs = dict(extra_kwargs or {})
    if force:
        kwargs["force"] = True
    started_at = timezone.now()
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        call_command(command_name, stdout=out_buf, stderr=err_buf, **kwargs)
        ts2 = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[alpha-scheduler] [{ts2}] {command_name} 本次执行结束。")
        finished_at = timezone.now()
        out_text = out_buf.getvalue()
        err_text = err_buf.getvalue()
        target_subject, target_date = _extract_mail_log_target_fields(out_text)
        _persist_mail_scheduler_run(
            command_name=command_name,
            ok=True,
            started_at=started_at,
            finished_at=finished_at,
            stdout_text=out_text,
            stderr_text=err_text,
            extra_kwargs=extra_kwargs,
            force=force,
            exc=None,
            target_subject=target_subject,
            target_date=target_date,
        )
        _send_scheduler_result_email(
            ok=True,
            command_name=command_name,
            started_at=started_at,
            finished_at=finished_at,
            stdout_text=out_text,
            stderr_text=err_text,
            exc=None,
            target_subject=target_subject,
            target_date=target_date,
        )
    except Exception as exc:
        ts2 = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[alpha-scheduler] [{ts2}] {command_name} 执行失败: {exc}")
        finished_at = timezone.now()
        out_text = out_buf.getvalue()
        err_text = err_buf.getvalue()
        target_subject, target_date = _extract_mail_log_target_fields(out_text)
        _persist_mail_scheduler_run(
            command_name=command_name,
            ok=False,
            started_at=started_at,
            finished_at=finished_at,
            stdout_text=out_text,
            stderr_text=err_text,
            extra_kwargs=extra_kwargs,
            force=force,
            exc=exc,
            target_subject=target_subject,
            target_date=target_date,
        )
        _send_scheduler_result_email(
            ok=False,
            command_name=command_name,
            started_at=started_at,
            finished_at=finished_at,
            stdout_text=out_text,
            stderr_text=err_text,
            exc=exc,
            target_subject=target_subject,
            target_date=target_date,
        )


def _dispatch_job_async(command_name: str, *, force: bool, extra_kwargs: dict | None = None) -> None:
    t = threading.Thread(
        target=_run_job,
        kwargs={
            "command_name": command_name,
            "force": force,
            "extra_kwargs": extra_kwargs,
        },
        name=f"alpha-job-{command_name}",
        daemon=True,
    )
    t.start()


def _should_skip_scheduled_job(command_name: str, *, today_iso: str) -> bool:
    if not is_trade_date_iso(today_iso):
        ts = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[alpha-scheduler] [{ts}] {today_iso} 非交易日，跳过 {command_name} 调度执行。"
        )
        return True
    if command_name == "auto_import_qichat_t0_mail":
        if not is_first_trading_day_of_iso_week(today_iso):
            ts = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"[alpha-scheduler] [{ts}] {today_iso} 非本周首个交易日，"
                f"跳过 {command_name}（T0 周度 CSV / 深度秩序）。"
            )
            return True
    return False


def run_scheduler_loop(
    *,
    poll_seconds: int,
    force: bool,
    run_now: bool,
) -> None:
    schedules = _schedules()
    for target, _, _ in schedules:
        try:
            datetime.strptime(target, "%H:%M")
        except ValueError as exc:
            raise RuntimeError(f"调度时间格式必须为 HH:MM，当前: {target}") from exc

    desc = ", ".join(f"{t}->{cmd}" for t, cmd, _ in schedules)
    print(
        f"[alpha-scheduler] 调度器已启动：{desc}（轮询 {poll_seconds}s）"
    )

    last_run_date: dict[str, str | None] = {f"{cmd}@{tm}": None for tm, cmd, _ in schedules}

    if run_now:
        for target, cmd_name, job_kwargs in schedules:
            today_iso = timezone.localdate().isoformat()
            if _should_skip_scheduled_job(cmd_name, today_iso=today_iso):
                continue
            _dispatch_job_async(cmd_name, force=force, extra_kwargs=job_kwargs)
            last_run_date[f"{cmd_name}@{target}"] = timezone.localdate().isoformat()

    while True:
        now_local = timezone.localtime()
        today = now_local.date().isoformat()
        hm = now_local.strftime("%H:%M")
        for target, cmd_name, job_kwargs in schedules:
            key = f"{cmd_name}@{target}"
            if hm == target and last_run_date.get(key) != today:
                if _should_skip_scheduled_job(cmd_name, today_iso=today):
                    last_run_date[key] = today
                    continue
                _dispatch_job_async(cmd_name, force=force, extra_kwargs=job_kwargs)
                last_run_date[key] = today
        time.sleep(max(5, int(poll_seconds)))


def start_scheduler_background() -> None:
    global _scheduler_started
    if _scheduler_started:
        return

    poll_seconds = 30
    force = False
    run_now = False

    t = threading.Thread(
        target=run_scheduler_loop,
        kwargs={
            "poll_seconds": poll_seconds,
            "force": force,
            "run_now": run_now,
        },
        name="alpha-mail-scheduler",
        daemon=True,
    )
    t.start()
    _scheduler_started = True
