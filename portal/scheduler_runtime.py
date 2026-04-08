from __future__ import annotations

import threading
import time
from datetime import datetime

from django.core.management import call_command
from django.utils import timezone

_scheduler_started = False


def _run_once(*, force: bool) -> None:
    ts = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[alpha-scheduler] [{ts}] 触发执行 auto_import_alpha_mail ...")
    kwargs = {"force": True} if force else {}
    call_command("auto_import_alpha_mail", **kwargs)
    ts2 = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[alpha-scheduler] [{ts2}] 本次执行结束。")


def run_scheduler_loop(*, target: str, poll_seconds: int, force: bool, run_now: bool) -> None:
    try:
        datetime.strptime(target, "%H:%M")
    except ValueError as exc:
        raise RuntimeError("--time 格式必须为 HH:MM，例如 17:30") from exc

    print(
        f"[alpha-scheduler] 调度器已启动：每天 {target} 执行 auto_import_alpha_mail（轮询 {poll_seconds}s）"
    )
    last_run_date: str | None = None

    if run_now:
        _run_once(force=force)
        last_run_date = timezone.localdate().isoformat()

    while True:
        now_local = timezone.localtime()
        today = now_local.date().isoformat()
        hm = now_local.strftime("%H:%M")
        if hm == target and last_run_date != today:
            _run_once(force=force)
            last_run_date = today
        time.sleep(max(5, int(poll_seconds)))


def start_scheduler_background() -> None:
    global _scheduler_started
    if _scheduler_started:
        return

    # 固定调度参数（不再读取环境变量）
    target = "17:30"
    poll_seconds = 30
    force = False
    run_now = False

    t = threading.Thread(
        target=run_scheduler_loop,
        kwargs={
            "target": target,
            "poll_seconds": poll_seconds,
            "force": force,
            "run_now": run_now,
        },
        name="alpha-mail-scheduler",
        daemon=True,
    )
    t.start()
    _scheduler_started = True

