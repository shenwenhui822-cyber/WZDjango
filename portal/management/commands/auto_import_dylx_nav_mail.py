"""
交易日 12:10 拉取 wangkan（ALPHA_MAIL_*）邮箱中主题
「【基金净值】SAJM64(总)_吾执多元量选私募证券投资基金_{YYYY-MM-DD}」
的邮件，从附件 xlsx 解析表头行净值表，写入 fund_nav_real.WZ_DYLX_MASTER。

IMAP：`.env` 中 ALPHA_MAIL_USER / ALPHA_MAIL_PASS、ALPHA_IMAP_SERVER、ALPHA_IMAP_PORT。

业务约定：仅运行日为交易日时执行；非交易日不执行、不通知。净值日 nav_date 默认取运行日之前
最近一个交易日（上一交易日，与 auto_import_dyyh_nav_mail / auto_import_slh_nav_mail 一致）。

调度：alpha_mail_scheduler 默认 12:10（环境变量 DYLX_NAV_MAIL_SCHEDULER_ENABLED）。

用法：
  python manage.py auto_import_dylx_nav_mail
  python manage.py auto_import_dylx_nav_mail --force
  python manage.py auto_import_dylx_nav_mail --nav-date 2026-05-07
"""
from __future__ import annotations

import imaplib
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.config.mail_imap import resolve_imap_credentials
from portal.services.dylx_nav_mail_service import (
    build_dylx_nav_mail_subject,
    get_dylx_fund_product,
)
from portal.services.fund_nav_real_service import parse_fund_nav_excel, upsert_fund_nav_doc
from portal.services.imap_common import find_latest_mail_id_by_exact_subject
from portal.services.mail_import_common import (
    imap_logout_safe,
    imap_open_inbox,
    save_excel_attachments_from_rfc822,
    send_alpha_notify_result_email,
    validate_mail_job_query_span,
)
from portal.services.trade_calendar_service import (
    is_trade_date_iso,
    prev_trading_day_iso_before,
)


def _pick_dylx_nav_xlsx(files: list[Path]) -> Path | None:
    """优先文件名含 SAJM64 / 吾执多元量选 / 【基金净值】的 Excel。"""
    if not files:
        return None
    scored: list[tuple[int, Path]] = []
    for p in files:
        name = p.name
        lower = name.lower()
        if not lower.endswith((".xlsx", ".xls", ".xlsm")):
            continue
        score = 0
        if "SAJM64" in name:
            score += 3
        if "吾执多元量选" in name:
            score += 3
        if "基金净值" in name:
            score += 2
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return files[0]


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行：抓取吾执多元量选 SAJM64(总) 基金净值邮件 xlsx 并写入 "
        "fund_nav_real.WZ_DYLX_MASTER；nav_date 默认为运行日之前最近一个交易日。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="忽略「运行日须为交易日」及 nav-date 的交易日校验，仍尝试导入。",
        )
        parser.add_argument(
            "--nav-date",
            default="",
            help="净值日 YYYY-MM-DD；默认取运行日之前最近一个交易日（本地时区）。",
        )

    def _send_result_email(
        self,
        report: dict[str, object],
        started_at,
        ended_at,
        base_url: str,
    ) -> None:
        duration = ended_at - started_at
        if isinstance(duration, timedelta):
            duration_sec = round(duration.total_seconds(), 3)
        else:
            duration_sec = 0.0
        status = str(report.get("status") or "UNKNOWN")
        mail_subject = (
            f"[{status}] 吾执多元量选净值邮件导入 "
            f"{timezone.localdate().strftime('%Y-%m-%d')}"
        )
        body = "\n".join(
            [
                "吾执多元量选 SAJM64(总)（fund_nav_real / WZ_DYLX_MASTER）自动导入结果",
                "",
                f"状态: {status}",
                f"开始时间: {timezone.localtime(started_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"结束时间: {timezone.localtime(ended_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"运行时长(秒): {duration_sec}",
                f"服务地址: {base_url or '-'}",
                f"目标净值日(nav_date): {report.get('nav_date') or '-'}",
                f"邮件主题: {report.get('target_subject') or '-'}",
                f"附件文件: {report.get('source_file') or '-'}",
                f"结果说明: {report.get('message') or '-'}",
                f"异常信息: {report.get('error') or '-'}",
            ]
        )
        send_alpha_notify_result_email(
            mail_subject=mail_subject,
            body=body,
            log_stdout=self.stdout.write,
            log_stderr_warn=lambda s: self.stderr.write(self.style.WARNING(s)),
        )

    def handle(self, *args, **options):
        started_at = timezone.now()
        base_url = getattr(settings, "PORTAL_RUN_ADDRESS", "")
        report: dict[str, object] = {
            "status": "UNKNOWN",
            "run_date": timezone.localdate().isoformat(),
            "nav_date": "",
            "notify": True,
            "target_subject": "",
            "source_file": "",
            "message": "",
            "error": "",
        }

        local_date = timezone.localdate()
        run_iso = local_date.isoformat()
        run_is_td = is_trade_date_iso(run_iso)

        self.stdout.write(f"运行日: {run_iso}，是否交易日: {run_is_td}")

        try:
            if not options["force"] and not run_is_td:
                report["status"] = "SKIPPED"
                report["notify"] = False
                report["message"] = (
                    f"{run_iso} 非交易日（trade_calendar），不执行、不发送结果邮件。"
                    " 使用 --force 可强制执行。"
                )
                self.stdout.write(self.style.WARNING(report["message"]))
                return

            nav_raw = (options["nav_date"] or "").strip()
            if nav_raw:
                nav_iso = nav_raw[:10]
            else:
                nav_iso = prev_trading_day_iso_before(run_iso) or ""
                if not nav_iso:
                    report["status"] = "FAILED"
                    report["message"] = (
                        "无法在 trade_calendar 中解析「运行日之前最近一个交易日」。"
                    )
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

            report["nav_date"] = nav_iso
            nav_td = is_trade_date_iso(nav_iso)
            target_subject = build_dylx_nav_mail_subject(nav_iso)
            report["target_subject"] = target_subject

            self.stdout.write(f"目标净值日(nav_date): {nav_iso}")

            span_err = validate_mail_job_query_span(
                query_iso=nav_iso,
                run_iso=run_iso,
                force=bool(options["force"]),
            )
            if span_err:
                report["status"] = "FAILED"
                report["message"] = span_err
                self.stderr.write(self.style.ERROR(span_err))
                return

            if nav_raw and not options["force"] and not nav_td:
                report["status"] = "SKIPPED"
                report["message"] = (
                    f"{nav_iso} 非交易日（trade_calendar），跳过。"
                    "使用 --force 可强制执行。"
                )
                self.stdout.write(self.style.WARNING(report["message"]))
                return

            fund = get_dylx_fund_product()
            email_user, email_pass, imap_server, imap_port = resolve_imap_credentials()
            self.stdout.write(f"IMAP: {email_user} @ {imap_server}:{imap_port}")

            ymd = nav_iso.replace("-", "")
            save_root = Path(settings.ALPHADATA_DIR) / "dylx_nav_mail" / ymd

            mailbox: imaplib.IMAP4_SSL | None = None
            try:
                mailbox = imap_open_inbox(
                    email_user, email_pass, imap_server, imap_port
                )
                self.stdout.write(f"主题: {target_subject}")
                mail_id = find_latest_mail_id_by_exact_subject(
                    mailbox, target_subject
                )
                if not mail_id:
                    report["status"] = "FAILED"
                    report["message"] = "未找到匹配主题的邮件。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                st2, msg_data = mailbox.fetch(mail_id, "(RFC822)")
                if st2 != "OK" or not msg_data or not msg_data[0]:
                    report["status"] = "FAILED"
                    report["message"] = "无法读取邮件正文。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                raw = msg_data[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    report["status"] = "FAILED"
                    report["message"] = "邮件内容格式异常。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                files = save_excel_attachments_from_rfc822(raw, save_root)
                if not files:
                    report["status"] = "FAILED"
                    report["message"] = "邮件中无 Excel 附件（.xlsx/.xls/.xlsm）。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                fp = _pick_dylx_nav_xlsx(files)
                if not fp:
                    report["status"] = "FAILED"
                    report["message"] = "未选择到附件。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                report["source_file"] = fp.name
                data = fp.read_bytes()
                doc = parse_fund_nav_excel(
                    data,
                    filename=fp.name,
                    fund=fund,
                    expected_nav_iso=nav_iso,
                )
                upsert_fund_nav_doc(
                    doc,
                    fund=fund,
                    source_subject=target_subject,
                )
                report["status"] = "SUCCESS"
                report["message"] = (
                    f"已落库 nav_date={doc['nav_date']} <- {fp.name}"
                )
                self.stdout.write(self.style.SUCCESS(report["message"]))
            finally:
                imap_logout_safe(mailbox)

        except Exception as exc:
            report["status"] = "FAILED"
            report["error"] = str(exc)
            report["message"] = str(exc)
            self.stderr.write(self.style.ERROR(f"执行失败: {exc}"))
            raise
        finally:
            if report.get("notify", True):
                self._send_result_email(report, started_at, timezone.now(), base_url)
