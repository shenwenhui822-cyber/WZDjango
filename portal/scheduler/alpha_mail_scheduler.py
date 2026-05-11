from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from django.core.management import call_command
from django.utils import timezone

from portal.services.trade_calendar_service import (
    is_first_trading_day_of_iso_week,
    is_trade_date_iso,
)

_scheduler_started = False

# (HH:MM, management command name, kwargs)
# 邮件类任务在各自命令内校验「查询日～运行日」闭区间交易日个数 ≤ MAIL_JOB_MAX_TRADING_DAY_SPAN（默认 3）。
# auto_import_qichat_t0_mail：IMAP 动态主题拉取上周 CSV + 入库（仅调度：每周首个交易日，见 _should_skip_scheduled_job）。
_DEFAULT_SCHEDULES: list[tuple[str, str, dict]] = [
    ("09:00", "auto_import_htzq_ht1_capital_mail", {}),
    ("09:31", "auto_import_fund_nav_mail", {}),
    ("09:33", "auto_import_ghzq_settle_mail", {}),
    ("11:00", "auto_import_slh_nav_mail", {}),
    ("11:35", "auto_import_dyyh_nav_mail", {}),
    ("09:30", "update_rq_bench", {}),
    ("12:00", "auto_import_zxdw_nav_mail", {}),
    ("12:10", "auto_import_dylx_nav_mail", {}),
    ("11:10", "auto_import_ysh_nav_mail", {}),
    ("11:40", "auto_import_jlh_nav_mail", {}),
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


def _jlh_nav_mail_enabled() -> bool:
    return os.getenv("JLH_NAV_MAIL_SCHEDULER_ENABLED", "1").strip() not in (
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
    if not _jlh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_jlh_nav_mail"]
    if not _ctayh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_ctayh_nav_mail"]
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
    try:
        call_command(command_name, **kwargs)
        ts2 = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[alpha-scheduler] [{ts2}] {command_name} 本次执行结束。")
    except Exception as exc:
        ts2 = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[alpha-scheduler] [{ts2}] {command_name} 执行失败: {exc}")


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
