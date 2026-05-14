"""
交易日 11:50（scheduler）拉取 wangkan（ALPHA_MAIL_*）邮箱中主题
「上海吾执投资管理有限公司净值表邮件{YYYYMMDD}【国信托管】」
的邮件，附件可为 zip（内 xlsx）或直接 xlsx；解析「图一」单日一行或「图二」多行中净值日
匹配行，写入 fund_nav_real.WZ_CTAYH_MASTER（产品代码 SNG191）。

IMAP：`.env` 中 ALPHA_MAIL_USER / ALPHA_MAIL_PASS、ALPHA_IMAP_SERVER、ALPHA_IMAP_PORT。

业务约定：仅运行日为交易日时执行；非交易日不执行、不通知。目标净值日 nav_date 默认取运行日之前
最近一个交易日（T-1）。

调度：alpha_mail_scheduler 默认 11:50（环境变量 CTAYH_NAV_MAIL_SCHEDULER_ENABLED）。

用法：
  python manage.py auto_import_ctayh_nav_mail
  python manage.py auto_import_ctayh_nav_mail --force
  python manage.py auto_import_ctayh_nav_mail --nav-date 2026-05-07
"""
from __future__ import annotations

import imaplib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.config.mail_imap import resolve_imap_credentials
from portal.services.ctayh_nav_mail_service import (
    build_ctayh_nav_mail_subject,
    get_ctayh_fund_product,
    pick_ctayh_nav_excel,
)
from portal.services.fund_nav_real_service import parse_fund_nav_excel, upsert_fund_nav_doc
from portal.services.imap_common import find_latest_mail_id_by_exact_subject
from portal.services.mail_import_common import (
    emit_mail_job_result_line,
    format_mail_job_notify_body,
    imap_logout_safe,
    imap_open_inbox,
    mail_job_notify_base,
    save_excel_zip_attachments_from_rfc822,
    send_alpha_notify_result_email,
    validate_mail_job_query_span,
)
from portal.services.trade_calendar_service import (
    is_trade_date_iso,
    prev_trading_day_iso_before,
)


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行：抓取 ALPHA_MAIL 收件箱 CTA 一号 SNG191 净值邮件（zip/xlsx）并写入 "
        "fund_nav_real.WZ_CTAYH_MASTER；nav_date 默认为运行日之前最近一个交易日。"
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
        status = str(report.get("status") or "UNKNOWN")
        mail_subject = (
            f"[{status}] CTA一号 SNG191 净值邮件导入 "
            f"{timezone.localdate().strftime('%Y-%m-%d')}"
        )
        title = "CTA一号 SNG191（fund_nav_real / WZ_CTAYH_MASTER）自动导入结果"
        data_ok = status == "SUCCESS"
        body = format_mail_job_notify_body(
            title=title,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            base_url=base_url,
            field_rows=[
                ("目标净值日(nav_date)", report.get("nav_date")),
                ("邮件主题", report.get("target_subject")),
                ("附件文件", report.get("source_file")),
                ("结果说明", report.get("message")),
                ("异常信息", report.get("error")),
            ],
        )
        snap = mail_job_notify_base(
            notify_title=title,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            base_url=base_url,
            data_import_succeeded=data_ok,
        )
        snap.update(
            {
                "nav_date": report.get("nav_date") or "",
                "target_subject": report.get("target_subject") or "",
                "source_file": report.get("source_file") or "",
                "message": report.get("message") or "",
                "error": report.get("error") or "",
            }
        )
        emit_mail_job_result_line(self.stdout.write, snap)
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
            ymd = nav_iso.replace("-", "")
            target_subject = build_ctayh_nav_mail_subject(ymd)
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

            email_user, email_pass, imap_server, imap_port = resolve_imap_credentials()
            self.stdout.write(f"IMAP: {email_user} @ {imap_server}:{imap_port}")

            save_root = Path(getattr(settings, "CTAYH_NAV_MAIL_ATTACH_DIR")) / ymd

            fund = get_ctayh_fund_product()
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

                files = save_excel_zip_attachments_from_rfc822(raw, save_root)
                if not files:
                    report["status"] = "FAILED"
                    report["message"] = "邮件中无 Excel 或 zip 附件。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                fp = pick_ctayh_nav_excel(files)
                if not fp:
                    report["status"] = "FAILED"
                    report["message"] = "解压后未找到 xlsx/xls。"
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
