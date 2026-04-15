"""
定时/手动：从米筐拉取指数行情写入 rq_bench。

默认（无参数）：仅当「运行日」为交易日时执行，导入「运行日」之前最近一个交易日的数据
（与 auto_import_fund_nav_mail 的交易日逻辑一致，见 alpha_mail_scheduler 09:31）。

用法：
  python manage.py update_rq_bench
  python manage.py update_rq_bench --force
  python manage.py update_rq_bench --trade-day 2026-04-14
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from smtplib import SMTPException, SMTP_SSL

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from portal.services.trade_calendar_service import (
    is_trade_date_iso,
    prev_trading_day_iso_before,
)

# 独立脚本位于 portal/management/update_rq_bench/
_BENCH_DIR = Path(__file__).resolve().parent.parent / "update_rq_bench"
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))
from update_rq_bench_17 import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    create_indexes_rq_bench,
    update_rq_bench,
)


class Command(BaseCommand):
    help = (
        "交易日从米筐更新 rq_bench（库表默认取 settings.MONGODB_RQ_BENCH_*）；"
        "默认仅运行日为交易日时执行，"
        "写入运行日之前最近一个交易日的行情。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="忽略「运行日须为交易日」判断，仍尝试按规则解析目标日并写入。",
        )
        parser.add_argument(
            "--trade-day",
            default="",
            help="指定要写入的行情日 YYYY-MM-DD；指定后不再使用「前一交易日」推导。",
        )
        parser.add_argument(
            "--db",
            default="",
            help="MongoDB 数据库名；不传则使用 settings.MONGODB_RQ_BENCH_DB",
        )
        parser.add_argument(
            "--coll",
            default="",
            help="MongoDB 集合名；不传则使用 settings.MONGODB_RQ_BENCH_COLLECTION",
        )

    def _send_result_email(
        self,
        report: dict[str, object],
        started_at,
        ended_at,
        base_url: str,
    ) -> None:
        to_raw = os.getenv("ALPHA_NOTIFY_TO", "")
        recipients = [x.strip() for x in to_raw.split(",") if x.strip()]
        smtp_host = os.getenv("ALPHA_NOTIFY_SMTP_HOST", "").strip()
        smtp_port = int(os.getenv("ALPHA_NOTIFY_SMTP_PORT", "465"))
        smtp_user = os.getenv("ALPHA_NOTIFY_USER", "").strip()
        smtp_pass = os.getenv("ALPHA_NOTIFY_PASS", "").strip()
        sender = os.getenv("ALPHA_NOTIFY_FROM", smtp_user).strip()

        if not recipients:
            self.stdout.write("未配置 ALPHA_NOTIFY_TO，跳过结果通知邮件。")
            return
        if not (smtp_host and smtp_user and smtp_pass and sender):
            self.stdout.write("通知邮箱 SMTP 配置不完整，跳过结果通知邮件。")
            return

        duration = ended_at - started_at
        if isinstance(duration, timedelta):
            duration_sec = round(duration.total_seconds(), 3)
        else:
            duration_sec = 0.0

        status = str(report.get("status") or "UNKNOWN")
        subject = f"[{status}] rq_bench 自动更新 {timezone.localdate().strftime('%Y-%m-%d')}"
        body = "\n".join(
            [
                "rq_bench 自动更新执行结果",
                "",
                f"状态: {status}",
                f"开始时间: {timezone.localtime(started_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"结束时间: {timezone.localtime(ended_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"运行时长(秒): {duration_sec}",
                f"服务地址: {base_url or '-'}",
                f"运行日: {report.get('run_date') or '-'}",
                f"运行日是否交易日: {report.get('run_day_is_trading')}",
                f"目标行情日: {report.get('target_trade_day') or '-'}",
                f"写入目标: {report.get('mongo_db')}.{report.get('target_coll')}",
                f"结果说明: {report.get('message') or '-'}",
                f"异常信息: {report.get('error') or '-'}",
            ]
        )

        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ";".join(recipients)
        msg.attach(MIMEText(body, "plain", "utf-8"))
        try:
            smtp = SMTP_SSL(smtp_host, smtp_port)
            smtp.login(smtp_user, smtp_pass)
            smtp.sendmail(sender, recipients, msg.as_string())
            smtp.quit()
            self.stdout.write(f"结果通知邮件已发送: {', '.join(recipients)}")
        except SMTPException as exc:
            self.stderr.write(self.style.WARNING(f"结果通知邮件发送失败: {exc}"))

    def handle(self, *args, **options):
        started_at = timezone.now()
        base_url = getattr(settings, "PORTAL_RUN_ADDRESS", "")
        force = bool(options["force"])
        mongo_db = (options["db"] or "").strip() or settings.MONGODB_RQ_BENCH_DB
        target_coll = (options["coll"] or "").strip() or settings.MONGODB_RQ_BENCH_COLLECTION
        trade_day_arg = (options["trade_day"] or "").strip()

        local_date = timezone.localdate()
        run_iso = local_date.isoformat()
        report: dict[str, object] = {
            "status": "UNKNOWN",
            "run_date": run_iso,
            "run_day_is_trading": is_trade_date_iso(run_iso),
            "target_trade_day": "",
            "mongo_db": mongo_db,
            "target_coll": target_coll,
            "message": "",
            "error": "",
            "notify": True,
        }

        try:
            if trade_day_arg:
                report["target_trade_day"] = trade_day_arg
                create_indexes_rq_bench(mongo_db=mongo_db)
                ok = update_rq_bench(
                    trade_day_arg,
                    mongo_db=mongo_db,
                    target_coll=target_coll,
                )
                if not ok:
                    raise CommandError("rq_bench 写入失败（指定 trade-day）。")
                report["status"] = "SUCCESS"
                report["message"] = "指定交易日写入成功。"
                return

            if not force and not report["run_day_is_trading"]:
                report["status"] = "SKIPPED"
                report["notify"] = False
                report["message"] = (
                    f"{run_iso} 非交易日（trade_calendar），跳过 rq_bench 更新。"
                )
                self.stdout.write(report["message"])
                return

            nav = prev_trading_day_iso_before(run_iso)
            if not nav:
                raise CommandError("无法解析前一交易日，请检查 trade_calendar 是否已导入。")

            report["target_trade_day"] = nav
            self.stdout.write(f"运行日 {run_iso}，目标行情日（前一交易日）: {nav}")
            create_indexes_rq_bench(mongo_db=mongo_db)
            ok = update_rq_bench(nav, mongo_db=mongo_db, target_coll=target_coll)
            if not ok:
                raise CommandError("rq_bench 写入失败。")
            report["status"] = "SUCCESS"
            report["message"] = "前一交易日写入成功。"
        except Exception as exc:
            report["status"] = "FAILED"
            report["error"] = str(exc)
            raise
        finally:
            if report.get("notify", True):
                self._send_result_email(report, started_at, timezone.now(), base_url)
