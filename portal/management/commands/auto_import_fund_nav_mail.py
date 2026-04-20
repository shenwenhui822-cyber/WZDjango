"""
T+1 早晨拉取「博士一号」真实净值邮件中的 Excel 附件（.xlsx / .xls），写入 MongoDB：fund_nav_real.WZ_BSYH_MASTER / fund_nav_real.WZ_BSYH_B。

业务约定：净值表在估值日 T 的 T+1 日约 6:30 到达；本任务在运行日 9:30 执行（见 alpha_mail_scheduler）。
仅当「运行日」为交易日时才执行；非交易日直接退出且不发送结果邮件。
目标净值日 nav_date：默认为「运行日」之前最近一个交易日（遇连续非交易日则继续往前查找）；
手工指定 --nav-date 时仍以该日为表格校验日；表格内「日期」须与 nav_date 一致。

用法：
  python manage.py auto_import_fund_nav_mail
  python manage.py auto_import_fund_nav_mail --force
  python manage.py auto_import_fund_nav_mail --nav-date 2026-04-02
"""
from __future__ import annotations

import email
import imaplib
import os
from datetime import timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from smtplib import SMTPException, SMTP_SSL

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.config.mail_imap import resolve_imap_credentials
from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, build_fund_nav_mail_subject
from portal.services.fund_nav_real_service import parse_fund_nav_excel, upsert_fund_nav_doc
from portal.services.imap_common import (
    decode_mime_header,
    find_latest_mail_id_by_exact_subject,
    normalize_attachment_filename,
)
from portal.services.trade_calendar_service import (
    is_trade_date_iso,
    prev_trading_day_iso_before,
)


def _excel_ext_ok(filename: str) -> bool:
    """识别 .xlsx / .xlsm / .xls（注意：不能用 endswith('.xls')，否则 .xlsx 会误判）。"""
    _, ext = os.path.splitext((filename or "").lower())
    return ext in (".xlsx", ".xls", ".xlsm")


def _save_excel_attachments_from_mail(msg_bytes: bytes, save_dir: Path) -> list[Path]:
    msg = email.message_from_bytes(msg_bytes)
    save_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for part in msg.walk():
        disp = str(part.get("Content-Disposition", ""))
        if "attachment" not in disp.lower():
            continue
        filename_raw = part.get_filename()
        filename = normalize_attachment_filename(
            decode_mime_header(filename_raw) if filename_raw else ""
        )
        if not _excel_ext_ok(filename):
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
    return saved


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行：抓取博士一号净值邮件 Excel（xlsx/xls）并写入 "
        "fund_nav_real.WZ_BSYH_MASTER / fund_nav_real.WZ_BSYH_B；"
        "nav_date 默认为运行日之前最近一个交易日。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="忽略「运行日须为交易日」及手工 nav-date 的交易日校验，仍尝试导入。",
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
        """与 auto_import_alpha_mail 相同：ALPHA_NOTIFY_* / SMTP_SSL。"""
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
        subject = (
            f"[{status}] 博士一号真实净值导入 "
            f"{timezone.localdate().strftime('%Y-%m-%d')}"
        )
        ok_lines = report.get("success_lines") or []
        fail_lines = report.get("failed_lines") or []
        body = "\n".join(
            [
                "博士一号真实净值（fund_nav_real / WZ_BSYH_MASTER、WZ_BSYH_B）自动导入结果",
                "",
                f"状态: {status}",
                f"开始时间: {timezone.localtime(started_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"结束时间: {timezone.localtime(ended_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"运行时长(秒): {duration_sec}",
                f"目标净值日(nav_date): {report.get('nav_date') or '-'}",
                f"运行日为交易日: {report.get('run_day_is_trading')}",
                f"nav_date 为交易日: {report.get('nav_date_is_trading')}",
                f"成功条数: {report.get('ok_count', 0)}",
                f"结果说明: {report.get('message') or '-'}",
                f"异常信息: {report.get('error') or '-'}",
                "",
                "成功明细:",
                *(ok_lines if isinstance(ok_lines, list) and ok_lines else ["- 无"]),
                "",
                "失败/缺失明细:",
                *(fail_lines if isinstance(fail_lines, list) and fail_lines else ["- 无"]),
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
        report: dict[str, object] = {
            "status": "UNKNOWN",
            "run_date": timezone.localdate().isoformat(),
            "nav_date": "",
            "run_day_is_trading": False,
            "nav_date_is_trading": False,
            "ok_count": 0,
            "success_lines": [],
            "failed_lines": [],
            "message": "",
            "error": "",
            "notify": True,
        }

        local_date = timezone.localdate()
        run_iso = local_date.isoformat()
        report["run_day_is_trading"] = is_trade_date_iso(run_iso)

        self.stdout.write(
            f"运行日: {run_iso}，运行日是否交易日: {report['run_day_is_trading']}"
        )
        if base_url:
            self.stdout.write(f"服务地址: {base_url}")

        try:
            if not options["force"] and not report["run_day_is_trading"]:
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
                        "无法在 trade_calendar 中解析「运行日之前最近一个交易日」，"
                        "请检查日历数据是否已导入。"
                    )
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return
            report["nav_date"] = nav_iso
            report["nav_date_is_trading"] = is_trade_date_iso(nav_iso)

            self.stdout.write(f"目标净值日(nav_date): {nav_iso}")

            if nav_raw and not options["force"] and not report["nav_date_is_trading"]:
                report["status"] = "SKIPPED"
                report["message"] = (
                    f"{nav_iso} 非交易日（trade_calendar），跳过。"
                    "使用 --force 可强制执行。"
                )
                self.stdout.write(self.style.WARNING(report["message"]))
                return

            email_user, email_pass, imap_server, imap_port = resolve_imap_credentials()
            self.stdout.write(f"IMAP: {email_user} @ {imap_server}:{imap_port}")

            save_root = Path(settings.ALPHADATA_DIR) / "fund_nav_mail" / nav_iso.replace(
                "-", ""
            )
            report_ok = 0
            report_fail: list[str] = []
            success_lines: list[str] = []

            mailbox: imaplib.IMAP4_SSL | None = None
            try:
                mailbox = imaplib.IMAP4_SSL(imap_server, imap_port)
                mailbox.login(email_user, email_pass)
                status, _ = mailbox.select("INBOX", readonly=True)
                if status != "OK":
                    raise RuntimeError("无法打开 INBOX")

                for fund in FUND_NAV_PRODUCTS:
                    subj = build_fund_nav_mail_subject(fund, nav_iso)
                    self.stdout.write(f"主题: {subj}")
                    mail_id = find_latest_mail_id_by_exact_subject(mailbox, subj)
                    if not mail_id:
                        msg = f"[{fund['product_key']}] 未找到邮件"
                        self.stdout.write(self.style.WARNING(msg))
                        report_fail.append(msg)
                        continue

                    st2, msg_data = mailbox.fetch(mail_id, "(RFC822)")
                    if st2 != "OK" or not msg_data or not msg_data[0]:
                        msg = f"[{fund['product_key']}] 无法读取邮件正文"
                        self.stdout.write(self.style.ERROR(msg))
                        report_fail.append(msg)
                        continue

                    raw = msg_data[0][1]
                    if not isinstance(raw, (bytes, bytearray)):
                        msg = f"[{fund['product_key']}] 邮件内容格式异常"
                        self.stdout.write(self.style.ERROR(msg))
                        report_fail.append(msg)
                        continue

                    files = _save_excel_attachments_from_mail(raw, save_root)
                    if not files:
                        msg = f"[{fund['product_key']}] 邮件中无 Excel 附件（.xlsx/.xls/.xlsm）"
                        self.stdout.write(self.style.WARNING(msg))
                        report_fail.append(msg)
                        continue

                    fp = files[0]
                    data = fp.read_bytes()
                    try:
                        doc = parse_fund_nav_excel(
                            data,
                            filename=fp.name,
                            fund=fund,
                            expected_nav_iso=nav_iso,
                        )
                        upsert_fund_nav_doc(
                            doc,
                            fund=fund,
                            source_subject=subj,
                        )
                    except Exception as exc:
                        msg = f"[{fund['product_key']}] 解析/落库失败: {exc}"
                        self.stdout.write(self.style.ERROR(msg))
                        report_fail.append(msg)
                        continue

                    line = (
                        f"- {fund['product_key']}: nav_date={doc['nav_date']}, "
                        f"subject={subj}"
                    )
                    success_lines.append(line)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[{fund['product_key']}] 已落库 <- {fp.name} nav_date={doc['nav_date']}"
                        )
                    )
                    report_ok += 1
            finally:
                if mailbox is not None:
                    try:
                        mailbox.logout()
                    except Exception:
                        pass

            report["ok_count"] = report_ok
            report["success_lines"] = success_lines
            report["failed_lines"] = report_fail
            n_funds = len(FUND_NAV_PRODUCTS)
            if report_ok == n_funds and not report_fail:
                report["status"] = "SUCCESS"
                report["message"] = f"完成，成功 {report_ok} 条。"
                self.stdout.write(self.style.SUCCESS(report["message"]))
            elif report_ok > 0:
                report["status"] = "PARTIAL"
                report["message"] = (
                    f"部分成功：{report_ok} 条，失败/缺失 {len(report_fail)}。"
                )
                self.stdout.write(self.style.WARNING(report["message"]))
            else:
                report["status"] = "FAILED"
                report["message"] = f"全部失败或缺失（共 {len(report_fail)} 项）。"
                self.stdout.write(self.style.ERROR(report["message"]))
        except Exception as exc:
            report["status"] = "FAILED"
            report["error"] = str(exc)
            report["message"] = str(exc)
            self.stderr.write(self.style.ERROR(f"执行失败: {exc}"))
            raise
        finally:
            if report.get("notify", True):
                self._send_result_email(report, started_at, timezone.now(), base_url)
