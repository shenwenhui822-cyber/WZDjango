"""
alpha 邮件类调度：并发执行队列（ThreadPoolExecutor）。

主循环只负责到点 submit，不阻塞在 call_command 上；多个任务重叠时在线程池内排队，
避免单任务过长导致后续任务无法启动；也不会无限制创建线程（见 ALPHA_MAIL_SCHEDULER_MAX_WORKERS）。
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def mail_scheduler_max_workers() -> int:
    raw = (os.getenv("ALPHA_MAIL_SCHEDULER_MAX_WORKERS") or "8").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 8
    return max(1, min(n, 64))


def get_mail_scheduler_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=mail_scheduler_max_workers(),
                thread_name_prefix="alpha-sched-cmd",
            )
        return _executor


def submit_mail_scheduler_job(job: Callable[[], None]) -> None:
    """将一次 manage.py 调度任务放入线程池；池满时在队列中等待，仍保证会执行。"""
    get_mail_scheduler_executor().submit(job)
