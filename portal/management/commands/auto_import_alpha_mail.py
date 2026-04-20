"""
交易日自动拉取 alpha 日报邮件（主题 alpha产品日报表YYYYMMDD）中的 xlsx 并导入 MongoDB。

设计为每日 17:30 由系统计划任务执行（仅运行日为交易日时拉取并导入；非交易日不执行、不发结果邮件）：
    python manage.py auto_import_alpha_mail

邮件：按主题精确匹配，多封同主题时取 Date 最新一封；仅处理 .xlsx 附件。
邮箱：见 portal.config.mail_imap（.env / 环境变量）。
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from smtplib import SMTPException, SMTP_SSL

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.config.mail_imap import resolve_imap_credentials
from portal.db.mongo import get_trade_date_collection
from portal.services.imap_common import (
    decode_mime_header,
    find_latest_mail_id_by_exact_subject,
    normalize_attachment_filename,
)
from portal.services.import_service import import_excel_fileobj


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行：抓取 alpha 日报邮件 xlsx 并导入 MongoDB；"
        "非交易日不执行且不发送结果邮件（建议每日 17:30 计划任务）"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="忽略「运行日须为交易日」判断，仍执行并发送结果邮件。",
        )
        parser.add_argument(
            "--subject-date",
            default="",
            help="邮件主题日期 YYYYMMDD，默认取当前本地日期（Asia/Shanghai）。",
        )

    def handle(self, *args, **options):
        started_at = timezone.now()
        report: dict[str, object] = {
            "status": "UNKNOWN",
            "run_date": timezone.localdate().isoformat(),
            "subject_date": "",
            "target_subject": "",
            "is_trading_day": False,
            "mail_found": False,
            "saved_files": [],
            "imported": [],
            "message": "",
            "error": "",
            "notify": True,
        }
        base_url = getattr(settings, "PORTAL_RUN_ADDRESS", "")
        if base_url:
            self.stdout.write(f"服务地址: {base_url}")

        local_date = timezone.localdate()
        subject_date = (options["subject_date"] or "").strip()
        if not subject_date:
            subject_date = local_date.strftime("%Y%m%d")
        report["subject_date"] = subject_date

        try:
            report["is_trading_day"] = self._is_trading_day(local_date)
            if not options["force"] and not report["is_trading_day"]:
                report["status"] = "SKIPPED"
                report["notify"] = False
                report["message"] = (
                    f"{local_date.isoformat()} 非交易日（trade_calendar），不执行、不发送结果邮件。"
                    " 使用 --force 可强制执行。"
                )
                self.stdout.write(report["message"])
                return

            email_user, email_pass, imap_server, imap_port = resolve_imap_credentials()
            self.stdout.write(f"IMAP: {email_user} @ {imap_server}:{imap_port}")

            target_subject = f"alpha产品日报表{subject_date}"
            report["target_subject"] = target_subject
            self.stdout.write(f"目标主题: {target_subject}")

            mailbox = None
            try:
                mailbox = imaplib.IMAP4_SSL(imap_server, imap_port)
                mailbox.login(email_user, email_pass)
                status, _ = mailbox.select("INBOX", readonly=True)
                if status != "OK":
                    raise RuntimeError("无法打开 INBOX")

                mail_id = find_latest_mail_id_by_exact_subject(mailbox, target_subject)
                if not mail_id:
                    report["status"] = "NO_MAIL"
                    report["message"] = "未找到目标邮件。"
                    self.stdout.write(self.style.WARNING(report["message"]))
                    return
                report["mail_found"] = True

                save_dir = (
                    Path(settings.BASE_DIR) / "Alphadata" / "auto_mail" / subject_date
                )
                files = self._save_xlsx_attachments(
                    mailbox, mail_id, save_dir, subject_date
                )
                report["saved_files"] = [str(x) for x in files]
                if not files:
                    report["status"] = "NO_XLSX"
                    report["message"] = "邮件中未找到 .xlsx 附件。"
                    self.stdout.write(self.style.WARNING(report["message"]))
                    return

                imported_stats: list[dict] = []
                for fp in files:
                    with fp.open("rb") as f:
                        stat = import_excel_fileobj(f, fp.name)
                    imported_stats.append(
                        {
                            "file": fp.name,
                            "inserted": stat.get("inserted"),
                            "sheets": stat.get("sheets"),
                        }
                    )
                    self.stdout.write(
                        f"导入成功: {fp.name} -> inserted={stat.get('inserted')}"
                    )
                report["imported"] = imported_stats
                report["status"] = "SUCCESS"
                report["message"] = f"完成: 共 {len(files)} 个 xlsx 已导入 MongoDB。"
                self.stdout.write(self.style.SUCCESS(report["message"]))
            finally:
                if mailbox is not None:
                    try:
                        mailbox.logout()
                    except Exception:
                        pass
        except Exception as exc:
            report["status"] = "FAILED"
            report["error"] = str(exc)
            self.stderr.write(self.style.ERROR(f"执行失败: {exc}"))
            raise
        finally:
            if report.get("notify", True):
                self._send_result_email(report, started_at, timezone.now(), base_url)

    def _is_trading_day(self, local_date) -> bool:
        coll = get_trade_date_collection()
        day = local_date.strftime("%Y-%m-%d")
        return coll.find_one({"trade_date": day}, {"_id": 1}) is not None

    def _save_xlsx_attachments(
        self,
        mailbox: imaplib.IMAP4_SSL,
        mail_id: str,
        save_dir: Path,
        subject_date: str,
    ) -> list[Path]:
        status, msg_data = mailbox.fetch(mail_id, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            return []
        msg = email.message_from_bytes(msg_data[0][1])
        save_dir.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        expected_pattern = re.compile(
            rf"^Alpha产品表现汇总_{re.escape(subject_date)}\.xlsx$",
            re.IGNORECASE,
        )
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" not in disp.lower():
                continue
            filename_raw = part.get_filename()
            filename = normalize_attachment_filename(
                decode_mime_header(filename_raw) if filename_raw else ""
            )
            if not filename.lower().endswith(".xlsx"):
                continue
            if not expected_pattern.match(filename):
                self.stdout.write(f"跳过非目标附件: {filename}")
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            output = save_dir / filename
            if output.exists():
                stem, ext = output.stem, output.suffix
                i = 1
                while True:
                    candidate = save_dir / f"{stem}_{i}{ext}"
                    if not candidate.exists():
                        output = candidate
                        break
                    i += 1
            output.write_bytes(payload)
            saved.append(output)
            self.stdout.write(f"已保存附件: {output}")
        return saved

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
        subject = f"[{status}] Alpha 日报自动导入 {timezone.localdate().strftime('%Y-%m-%d')}"
        imported = report.get("imported") or []
        imported_lines = []
        for row in imported:
            if isinstance(row, dict):
                imported_lines.append(
                    f"- {row.get('file')}: inserted={row.get('inserted')}"
                )
        body = "\n".join(
            [
                "Alpha 日报自动导入执行结果",
                "",
                f"状态: {status}",
                f"开始时间: {timezone.localtime(started_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"结束时间: {timezone.localtime(ended_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"运行时长(秒): {duration_sec}",
                f"交易日: {report.get('is_trading_day')}",
                f"主题日期: {report.get('subject_date')}",
                f"目标主题: {report.get('target_subject')}",
                f"命中邮件: {report.get('mail_found')}",
                f"下载附件数: {len(report.get('saved_files') or [])}",
                f"导入文件数: {len(imported)}",
                f"结果说明: {report.get('message') or '-'}",
                f"异常信息: {report.get('error') or '-'}",
                "",
                "导入明细:",
                *(imported_lines or ["- 无"]),
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
