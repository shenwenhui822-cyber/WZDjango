
from __future__ import annotations

import functools
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
from portal.scheduler.alpha_mail_job_runner import submit_mail_scheduler_job
from portal.scheduler.alpha_mail_schedule import get_active_mail_scheduler_schedules
from portal.services.mail_import_common import (
    send_alpha_notify_result_email,
    strip_mail_job_result_json,
)
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


def _normalize_target_date_ymd(val: object) -> str | None:
    """将 nav_date / YYYY-MM-DD / YYYYMMDD 等统一为 YYYYMMDD。"""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{8}", s) and _valid_calendar_ymd(s):
        return s
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m_iso:
        compact = "".join(m_iso.groups())
        if _valid_calendar_ymd(compact):
            return compact
    return None


def _extract_mail_log_target_fields(stdout_text: str) -> tuple[str | None, str | None]:
    """从命令 stdout 解析「目标主题」「目标日期」，写入 MAIL_LOGS（目标日期统一为 YYYYMMDD 字符串）。"""
    text = stdout_text or ""
    target_subject: str | None = None
    for pat in (
        r"^\s*目标主题[:：]\s*(.+?)(?:\r?\n|$)",
        r"^\s*主题[:：]\s*(.+?)(?:\r?\n|$)",
        r"^\s*已命中主题[:：]\s*(.+?)(?:\r?\n|$)",
    ):
        m_sub = re.search(pat, text, flags=re.MULTILINE)
        if m_sub:
            target_subject = (m_sub.group(1) or "").strip() or None
            if target_subject:
                break

    target_date: str | None = None
    m_day = re.search(r"主题日\s+(\d{8})\b", text)
    if m_day and _valid_calendar_ymd(m_day.group(1)):
        target_date = m_day.group(1)
    if target_date is None:
        m_rep = re.search(r"报告日\D*(\d{8})\b", text)
        if m_rep and _valid_calendar_ymd(m_rep.group(1)):
            target_date = m_rep.group(1)
    if target_date is None:
        for pat in (
            r"目标净值日\(nav_date\)[:：]\s*(\d{4}-\d{2}-\d{2})",
            r"目标行情日[^:：\n]{0,24}[:：]\s*(\d{4}-\d{2}-\d{2})",
            r"报告日\(report_date\)[:：]\s*(\d{4}-\d{2}-\d{2})",
            r"主题日期\(ymd\)[:：]\s*(\d{8})",
        ):
            m = re.search(pat, text)
            if not m:
                continue
            target_date = _normalize_target_date_ymd(m.group(1))
            if target_date:
                break
    if target_date is None and target_subject:
        m_iso = re.search(r"(\d{4}-\d{2}-\d{2})", target_subject)
        if m_iso:
            target_date = _normalize_target_date_ymd(m_iso.group(1))
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


def _merge_mail_log_target_fields_from_notify_snapshot(
    target_subject: str | None,
    target_date: str | None,
    notify_snapshot: dict | None,
) -> tuple[str | None, str | None]:
    """stdout 未解析到时，从 __MAIL_LOG_RESULT_JSON__ 回填 target_subject / target_date。"""
    if not notify_snapshot:
        return target_subject, target_date

    if not target_subject:
        for key in ("target_subject", "matched_subject"):
            raw = notify_snapshot.get(key)
            if raw is not None and str(raw).strip():
                target_subject = str(raw).strip()
                break

    if not target_date:
        for key in (
            "target_date",
            "nav_date",
            "report_date",
            "ymd",
            "target_trade_day",
            "subject_date",
            "statement_date",
        ):
            ymd = _normalize_target_date_ymd(notify_snapshot.get(key))
            if ymd:
                target_date = ymd
                break

    if not target_date and target_subject:
        _, inferred = _extract_mail_log_target_fields(f"目标主题: {target_subject}\n")
        target_date = inferred

    return target_subject, target_date


def _resolve_mail_log_target_fields(
    stdout_text: str,
    notify_snapshot: dict | None,
) -> tuple[str | None, str | None]:
    """统一解析 MAIL_LOGS 的 target_subject / target_date（stdout + notify_snapshot）。"""
    target_subject, target_date = _extract_mail_log_target_fields(stdout_text)
    return _merge_mail_log_target_fields_from_notify_snapshot(
        target_subject, target_date, notify_snapshot
    )


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

# 命令正常退出（无异常）但 stdout/stderr 出现下列片段时，视为未成功拉取/入库（见 _infer_scheduled_job_outcome）。
_SCHED_SKIP_MARKERS: tuple[str, ...] = (
    "非交易日（trade_calendar），不执行",
    "非交易日（trade_calendar），跳过",
    "跳过 rq_bench 更新",
)
_SCHED_FAILURE_MARKERS: tuple[str, ...] = (
    "未找到目标邮件",
    "未找到匹配主题的邮件",
    "未找到完整主题精确匹配的邮件",
    "未找到主题完全匹配的邮件",
    "未找到 INTERNALDATE 为",
    "邮件中未找到 .xlsx 附件",
    "邮件中无 Excel 附件",
    "邮件中无 CSV 附件（Content-Disposition: attachment）",
    "无法读取邮件正文",
    "邮件内容格式异常",
    "未找到目标 xlsx 附件",
    "未找到 STZ053",
    "解压后未找到 xlsx/xls",
    "未找到「普通账单_HT1」",
    "推算的上一自然周内无交易日历记录",
    "请缩小日期范围或使用 --force",
    "查询日期或运行日格式无效",
    "未找到邮件",
    "未选择到附件。",
)


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text or "")


def _first_line_containing(text: str, needle: str) -> str | None:
    for line in text.splitlines():
        if needle in line:
            s = line.strip()
            return s[:800] if len(s) > 800 else s
    return None


def _infer_scheduled_job_outcome(
    command_name: str,
    stdout_text: str,
    stderr_text: str,
) -> tuple[str, str | None]:
    """
    在 call_command 未抛异常时，根据输出推断是否真正完成数据拉取/入库。
    返回 (log_type, failure_reason)，log_type ∈ success | failure | skipped。
    """
    out = _strip_ansi(stdout_text or "")
    err = _strip_ansi(stderr_text or "")
    comb = f"{out}\n{err}"
    for marker in _SCHED_SKIP_MARKERS:
        if marker in comb:
            return "skipped", _first_line_containing(comb, marker) or marker
    for marker in _SCHED_FAILURE_MARKERS:
        if marker in comb:
            return "failure", _first_line_containing(comb, marker) or marker
    if command_name == "sync_t0_performance" and re.search(
        r"完成：\s*处理\s*0\s*个文件", comb
    ):
        return "failure", "sync_t0_performance：FTP 未处理到任何文件（0 个）。"
    if command_name == "sync_t0_performance" and re.search(r"upsert\s*0\s*行", comb):
        return (
            "failure",
            _first_line_containing(comb, "完成") or "sync_t0_performance：upsert 0 行，未写入数据。",
        )
    if command_name == "auto_import_qichat_t0_mail" and re.search(
        r"入库完成[^\n]*upsert\s*0\s*行", comb
    ):
        return (
            "failure",
            _first_line_containing(comb, "入库完成") or "qichat 周度 CSV：upsert 0 行。",
        )
    return "success", None


# 仅用于 MAIL_LOGS / 调度元数据，不会传给 manage.py（见 _split_scheduler_call_kwargs）。
_SCHEDULER_META_KEYS: frozenset[str] = frozenset({"scheduler_job_key"})


def _split_scheduler_call_kwargs(extra: dict | None) -> tuple[dict, str | None]:
    """返回 (传给 call_command 的 kwargs, scheduler_job_key)。"""
    raw = dict(extra or {})
    job_key = raw.pop("scheduler_job_key", None)
    sk = str(job_key).strip() if job_key is not None else ""
    for k in list(raw.keys()):
        if k in _SCHEDULER_META_KEYS:
            raw.pop(k, None)
    return raw, (sk or None)


def _notify_snapshot_for_mongo(snapshot: dict | None) -> dict[str, object] | None:
    if not snapshot:
        return None
    return {str(k): bson_safe_value(v) for k, v in snapshot.items()}


def _merge_outcome_with_notify_snapshot(
    outcome: str,
    soft_reason: str | None,
    notify_snapshot: dict | None,
) -> tuple[str, bool, str | None]:
    """结合 stdout 推断与命令上报的 data_import_succeeded，得到 (outcome, import_ok, failure_reason)。"""
    import_ok = outcome == "success"
    fr = soft_reason
    if notify_snapshot and "data_import_succeeded" in notify_snapshot:
        import_ok = bool(notify_snapshot["data_import_succeeded"])
        if import_ok:
            outcome = "success"
            fr = None
        else:
            if outcome != "skipped":
                outcome = "failure"
            if not fr:
                fr = str(
                    notify_snapshot.get("message")
                    or notify_snapshot.get("status")
                    or "未落库成功(data_import_succeeded=false)"
                )
    return outcome, import_ok, fr


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
    log_type: str,
    import_succeeded: bool,
    started_at,
    finished_at,
    stdout_text: str,
    stderr_text: str,
    extra_kwargs: dict | None,
    force: bool,
    exc: BaseException | None = None,
    target_subject: str | None = None,
    target_date: str | None = None,
    failure_reason: str | None = None,
    scheduler_job_key: str | None = None,
    notify_snapshot: dict | None = None,
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
        doc: dict[str, object] = {
            "log_type": log_type,
            "import_succeeded": import_succeeded,
            "command_name": command_name,
            "scheduler_job_key": scheduler_job_key,
            "started_at": started_at,
            "finished_at": finished_at,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "kwargs": kwargs_log,
            "target_subject": target_subject,
            "target_date": target_date,
            "failure_reason": failure_reason,
        }
        ns = _notify_snapshot_for_mongo(notify_snapshot)
        if ns is not None:
            doc["notify_snapshot"] = ns
        if log_type == "failure":
            if exc is not None:
                tb = traceback.format_exc()
                tb = _truncate_log_text(tb, max_len)
                doc["error_type"] = type(exc).__name__
                doc["error_message"] = str(exc)
                doc["traceback"] = tb
            else:
                doc["error_type"] = "ImportOutcome"
                doc["error_message"] = failure_reason or ""
                doc["traceback"] = ""
        coll.insert_one(doc)
    except Exception as log_exc:
        ts = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[alpha-scheduler] [{ts}] 写入 mail_logs 运行记录失败（不影响任务）: {log_exc}"
        )


def _send_scheduler_result_email(
    *,
    outcome: str,
    command_name: str,
    started_at,
    finished_at,
    stdout_text: str,
    stderr_text: str,
    exc: BaseException | None = None,
    target_subject: str | None = None,
    target_date: str | None = None,
    failure_reason: str | None = None,
    scheduler_job_key: str | None = None,
    notify_snapshot: dict | None = None,
) -> None:
    if not _scheduler_result_alpha_notify_enabled():
        return
    max_body = _scheduler_result_email_body_max_chars()
    st = timezone.localtime(started_at).strftime("%Y-%m-%d %H:%M:%S")
    et = timezone.localtime(finished_at).strftime("%Y-%m-%d %H:%M:%S")
    label = {"success": "成功", "failure": "失败", "skipped": "跳过"}.get(
        outcome, outcome
    )
    parts = [
        "alpha_mail_scheduler 定时任务汇总（完整日志见 mail_logs.MAIL_LOGS）",
        "",
        f"命令: {command_name}",
        f"调度任务键: {scheduler_job_key or '-'}",
        f"结果: {label}",
        f"开始(本地): {st}",
        f"结束(本地): {et}",
    ]
    if failure_reason:
        parts.append(f"导入/同步说明: {failure_reason}")
    if notify_snapshot:
        st = str(notify_snapshot.get("status") or "-")
        dis = notify_snapshot.get("data_import_succeeded")
        parts.append(f"任务结果摘要(status={st}, data_import_succeeded={dis})")
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
    if outcome == "failure" and exc is not None:
        parts.extend(["", "--- exception ---", f"{type(exc).__name__}: {exc}"])
    body = _truncate_log_text("\n".join(parts), max_body)
    tag = {"success": "OK", "failure": "FAILED", "skipped": "SKIP"}.get(
        outcome, "UNKNOWN"
    )
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

# 默认任务时刻表与各任务环境开关见 portal.scheduler.alpha_mail_schedule。
# 到点派发不阻塞：任务在线程池内执行/排队（portal.scheduler.alpha_mail_job_runner，并发数 ALPHA_MAIL_SCHEDULER_MAX_WORKERS，默认 8）。


def _run_job(command_name: str, *, force: bool, extra_kwargs: dict | None = None) -> None:
    ts = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[alpha-scheduler] [{ts}] 触发执行 {command_name} ...")
    call_kw, sched_job_key = _split_scheduler_call_kwargs(extra_kwargs)
    kwargs = dict(call_kw)
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
        out_text_raw = out_buf.getvalue()
        out_text, notify_snapshot = strip_mail_job_result_json(out_text_raw)
        err_text = err_buf.getvalue()
        target_subject, target_date = _resolve_mail_log_target_fields(
            out_text, notify_snapshot
        )
        outcome, soft_reason = _infer_scheduled_job_outcome(
            command_name, out_text, err_text
        )
        outcome, import_ok, soft_reason = _merge_outcome_with_notify_snapshot(
            outcome, soft_reason, notify_snapshot
        )
        _persist_mail_scheduler_run(
            command_name=command_name,
            log_type=outcome,
            import_succeeded=import_ok,
            started_at=started_at,
            finished_at=finished_at,
            stdout_text=out_text,
            stderr_text=err_text,
            extra_kwargs=extra_kwargs,
            force=force,
            exc=None,
            target_subject=target_subject,
            target_date=target_date,
            failure_reason=None if import_ok else soft_reason,
            scheduler_job_key=sched_job_key,
            notify_snapshot=notify_snapshot,
        )
        _send_scheduler_result_email(
            outcome=outcome,
            command_name=command_name,
            started_at=started_at,
            finished_at=finished_at,
            stdout_text=out_text,
            stderr_text=err_text,
            exc=None,
            target_subject=target_subject,
            target_date=target_date,
            failure_reason=None if import_ok else soft_reason,
            scheduler_job_key=sched_job_key,
            notify_snapshot=notify_snapshot,
        )
    except Exception as exc:
        ts2 = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[alpha-scheduler] [{ts2}] {command_name} 执行失败: {exc}")
        finished_at = timezone.now()
        out_text_raw = out_buf.getvalue()
        out_text, notify_snapshot = strip_mail_job_result_json(out_text_raw)
        err_text = err_buf.getvalue()
        target_subject, target_date = _resolve_mail_log_target_fields(
            out_text, notify_snapshot
        )
        fr = str(exc)
        _persist_mail_scheduler_run(
            command_name=command_name,
            log_type="failure",
            import_succeeded=False,
            started_at=started_at,
            finished_at=finished_at,
            stdout_text=out_text,
            stderr_text=err_text,
            extra_kwargs=extra_kwargs,
            force=force,
            exc=exc,
            target_subject=target_subject,
            target_date=target_date,
            failure_reason=fr,
            scheduler_job_key=sched_job_key,
            notify_snapshot=notify_snapshot,
        )
        _send_scheduler_result_email(
            outcome="failure",
            command_name=command_name,
            started_at=started_at,
            finished_at=finished_at,
            stdout_text=out_text,
            stderr_text=err_text,
            exc=exc,
            target_subject=target_subject,
            target_date=target_date,
            failure_reason=fr,
            scheduler_job_key=sched_job_key,
            notify_snapshot=notify_snapshot,
        )


def _dispatch_job_async(command_name: str, *, force: bool, extra_kwargs: dict | None = None) -> None:
    submit_mail_scheduler_job(
        functools.partial(
            _run_job,
            command_name,
            force=force,
            extra_kwargs=extra_kwargs,
        )
    )


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
    schedules = get_active_mail_scheduler_schedules()
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
