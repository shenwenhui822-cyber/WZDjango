from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from django.core.management import call_command
from django.utils import timezone

_scheduler_started = False

# (HH:MM, management command name, kwargs)
_DEFAULT_SCHEDULES: list[tuple[str, str, dict]] = [
    ("09:30", "auto_import_fund_nav_mail", {}),
    ("17:30", "auto_import_alpha_mail", {}),
]


def _fund_nav_enabled() -> bool:
    return os.getenv("FUND_NAV_SCHEDULER_ENABLED", "1").strip() not in (
        "0",
        "false",
        "False",
    )


def _schedules() -> list[tuple[str, str, dict]]:
    if not _fund_nav_enabled():
        return [x for x in _DEFAULT_SCHEDULES if x[1] != "auto_import_fund_nav_mail"]
    return list(_DEFAULT_SCHEDULES)


def _run_job(command_name: str, *, force: bool) -> None:
    ts = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[alpha-scheduler] [{ts}] 触发执行 {command_name} ...")
    kwargs = {"force": True} if force else {}
    call_command(command_name, **kwargs)
    ts2 = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[alpha-scheduler] [{ts2}] {command_name} 本次执行结束。")


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
        for target, cmd_name, _ in schedules:
            _run_job(cmd_name, force=force)
            last_run_date[f"{cmd_name}@{target}"] = timezone.localdate().isoformat()

    while True:
        now_local = timezone.localtime()
        today = now_local.date().isoformat()
        hm = now_local.strftime("%H:%M")
        for target, cmd_name, _ in schedules:
            key = f"{cmd_name}@{target}"
            if hm == target and last_run_date.get(key) != today:
                _run_job(cmd_name, force=force)
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
