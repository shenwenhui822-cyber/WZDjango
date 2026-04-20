"""
交易日中午前后：按「前一交易日」报告日期匹配邮件主题，下载净值 Excel，
按产品代码写入 fund_nav_real 下 WZ_ZXDW_MASTER / WZ_ZXDW_A|B|C（见 settings.MONGODB_ZXDW_NAV_COLLECTIONS）。

调度：portal.scheduler.alpha_mail_scheduler 默认 12:00（环境变量 ZXDW_NAV_MAIL_SCHEDULER_ENABLED）。

业务约定：运行日中午拉取「前一交易日」净值表；主题中须含该日 YYYYMMDD + 基金关键词之一
（见本模块常量 ZXDW_NAV_MAIL_FUND_KEY_PHRASES）。
邮箱登录账号从 wzproject/secure_config.pyd 的 get_config() 读取。
任务结束后按 ALPHA_NOTIFY_* 发送结果邮件（非交易日跳过时不发）。

用法：
  python manage.py auto_import_zxdw_nav_mail
  python manage.py auto_import_zxdw_nav_mail --force
  python manage.py auto_import_zxdw_nav_mail --report-date 2026-04-15
"""
from __future__ import annotations

import email
import imaplib
import os
from datetime import timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from importlib import import_module
from pathlib import Path
from smtplib import SMTPException, SMTP_SSL

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.services.imap_common import (
    decode_mime_header,
    normalize_attachment_filename,
)
from portal.services.trade_calendar_service import is_trade_date_iso, prev_trading_day_iso_before
from portal.services.zxdw_fund_nav_service import import_zxdw_excel_routed_by_product_code

# 主题匹配规则：固定前缀 + 报告日 YYYYMMDD
ZXDW_NAV_MAIL_FUND_KEY_PHRASES: tuple[str, ...] = (
    "【净值表】上海吾执投资管理有限公司吾执泽鑫多维产品净值表发送-管理人",
)


def _excel_ext_ok(filename: str) -> bool:
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


def _resolve_imap_credentials_from_secure_config() -> tuple[str, str, str, int]:
    """
    从 wzproject/secure_config.pyd 读取邮箱配置。

    约定 get_config() 返回 dict，键名兼容：
    - email_address / password
    - imap_server(默认 imap.exmail.qq.com) / imap_port(默认 993)
    """
    try:
        secure_mod = import_module("wzproject.secure_config")
    except Exception as exc:
        raise RuntimeError(
            "无法加载 wzproject.secure_config（secure_config.pyd），请确认文件存在且可导入。"
        ) from exc

    get_config = getattr(secure_mod, "get_config", None)
    if get_config is None:
        raise RuntimeError("wzproject.secure_config 未提供 get_config()。")

    cfg = get_config() or {}
    user = str(cfg.get("email_address") or "").strip()
    pwd = str(cfg.get("password") or "").strip()
    host = str(cfg.get("imap_server") or "imap.exmail.qq.com").strip()
    port = int(cfg.get("imap_port") or 993)
    if not (user and pwd):
        raise RuntimeError("secure_config 中 email_address/password 未配置。")
    return user, pwd, host, port


def _find_first_mail_id_by_exact_subject(
    mailbox: imaplib.IMAP4_SSL,
    target_subject: str,
) -> str | None:
    """
    按完整主题精确匹配，命中第一封（按邮件 ID 倒序，优先较新）后立即结束扫描。
    """
    status, data = mailbox.search(None, "ALL")
    if status != "OK" or not data or not data[0]:
        return None

    for raw_id in reversed(data[0].split()):
        mail_id = raw_id.decode()
        status, msg_data = mailbox.fetch(mail_id, "(BODY[HEADER.FIELDS (SUBJECT)])")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_mime_header(msg.get("Subject", "")).strip()
        if subject == target_subject.strip():
            return mail_id
    return None


class Command(BaseCommand):
    help = (
        "交易日执行：IMAP 查找 ZXDW 净值邮件，附件 Excel 按产品代码写入 "
        "fund_nav_real 下 WZ_ZXDW_* 集合"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="忽略「运行日须为交易日」校验，仍尝试拉取并导入。",
        )
        parser.add_argument(
            "--report-date",
            default="",
            help="报告日期 YYYY-MM-DD；默认取运行日之前最近一个交易日（前一交易日净值日）。",
        )

    def _send_result_email(
        self,
        report: dict[str, object],
        started_at,
        ended_at,
        base_url: str,
    ) -> None:
        """与 auto_import_fund_nav_mail 相同：ALPHA_NOTIFY_* / SMTP_SSL。"""
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
            f"[{status}] ZXDW 泽鑫多维净值邮件导入 "
            f"{timezone.localdate().strftime('%Y-%m-%d')}"
        )
        warn_lines = report.get("warn_lines") or []
        ok_lines = report.get("success_lines") or []
        body = "\n".join(
            [
                "ZXDW 净值（fund_nav_real / WZ_ZXDW_*）邮件自动导入结果",
                "",
                f"状态: {status}",
                f"开始时间: {timezone.localtime(started_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"结束时间: {timezone.localtime(ended_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"运行时长(秒): {duration_sec}",
                f"报告日(report_date): {report.get('report_date') or '-'}",
                f"主题日期(ymd): {report.get('ymd') or '-'}",
                f"累计 upsert 条数: {report.get('total_upsert', 0)}",
                f"结果说明: {report.get('message') or '-'}",
                f"异常信息: {report.get('error') or '-'}",
                "",
                "成功/处理明细:",
                *(ok_lines if isinstance(ok_lines, list) and ok_lines else ["- 无"]),
                "",
                "告警/解析问题（若有）:",
                *(
                    warn_lines
                    if isinstance(warn_lines, list) and warn_lines
                    else ["- 无"]
                ),
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
            "notify": True,
            "report_date": "",
            "ymd": "",
            "message": "",
            "error": "",
            "total_upsert": 0,
            "success_lines": [],
            "warn_lines": [],
        }
        mailbox: imaplib.IMAP4_SSL | None = None
        try:
            force = bool(options.get("force"))
            today_iso = timezone.localdate().isoformat()
            if not force and not is_trade_date_iso(today_iso):
                report["status"] = "SKIPPED"
                report["notify"] = False
                report["message"] = (
                    f"{today_iso} 非交易日，不执行、不发送结果邮件。"
                    " 使用 --force 可强制执行。"
                )
                self.stdout.write(self.style.WARNING(report["message"]))
                return

            raw_report = (options.get("report_date") or "").strip()
            if raw_report:
                report_iso = raw_report[:10]
                ymd = report_iso.replace("-", "")
            else:
                report_iso = prev_trading_day_iso_before(today_iso) or ""
                if not report_iso:
                    report["status"] = "FAILED"
                    report["message"] = "无法解析前一交易日，请指定 --report-date"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return
                ymd = report_iso.replace("-", "")

            report["report_date"] = report_iso
            report["ymd"] = ymd

            subject_prefixes = ZXDW_NAV_MAIL_FUND_KEY_PHRASES
            if not subject_prefixes:
                report["status"] = "FAILED"
                report["message"] = "ZXDW_NAV_MAIL_FUND_KEY_PHRASES 为空，请在本文件顶部配置"
                self.stderr.write(self.style.ERROR(report["message"]))
                return
            target_subjects = [f"{prefix}{ymd}" for prefix in subject_prefixes]

            user, pwd, host, port = _resolve_imap_credentials_from_secure_config()
            try:
                mailbox = imaplib.IMAP4_SSL(host, port)
                mailbox.login(user, pwd)
                st, _ = mailbox.select("INBOX", readonly=True)
                if st != "OK":
                    report["status"] = "FAILED"
                    report["message"] = "无法打开 INBOX"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                self.stdout.write("按完整主题精确匹配邮件（非模糊匹配）...")
                for sub in target_subjects:
                    self.stdout.write(f"目标主题：{sub}")
                mail_id = None
                matched_subject = ""
                for sub in target_subjects:
                    mail_id = _find_first_mail_id_by_exact_subject(mailbox, sub)
                    if mail_id:
                        matched_subject = sub
                        break
                if not mail_id:
                    report["status"] = "FAILED"
                    report["message"] = "未找到完整主题精确匹配的邮件"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return
                self.stdout.write(self.style.SUCCESS(f"已命中主题：{matched_subject}"))

                st, msg_data = mailbox.fetch(mail_id, "(RFC822)")
                if st != "OK" or not msg_data or not msg_data[0]:
                    report["status"] = "FAILED"
                    report["message"] = "读取邮件正文失败"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return
                msg_bytes = msg_data[0][1]

                save_root = Path(
                    getattr(
                        settings,
                        "ZXDW_NAV_MAIL_ATTACH_DIR",
                        settings.BASE_DIR / "downloaded_attachments_zxdw_nav",
                    )
                )
                day_dir = save_root / ymd
                files = _save_excel_attachments_from_mail(msg_bytes, day_dir)
                if not files:
                    report["status"] = "FAILED"
                    report["message"] = "邮件中无 Excel 附件（.xlsx/.xls/.xlsm）"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return
                self.stdout.write(self.style.SUCCESS(f"附件已保存到目录：{day_dir}"))

                subj = f"imap:{ymd}"
                total_upsert = 0
                success_lines: list[str] = []
                warn_lines: list[str] = []
                for fp in files:
                    data = fp.read_bytes()
                    stat = import_zxdw_excel_routed_by_product_code(
                        data,
                        filename=fp.name,
                        source_subject=subj,
                    )
                    errs = stat.get("errors") or []
                    for e in errs[:20]:
                        line = f"{fp.name}: {e}"
                        warn_lines.append(line)
                        self.stderr.write(self.style.WARNING(line))
                    n = int(stat.get("upserted_total") or 0)
                    total_upsert += n
                    pc = stat.get("per_collection", {})
                    success_lines.append(
                        f"- {fp.name}: upsert {n} 条，分集合 {pc}"
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"{fp.name} 写入合计 {n} 条，分集合 {pc}"
                        )
                    )

                report["total_upsert"] = total_upsert
                report["success_lines"] = success_lines
                report["warn_lines"] = warn_lines
                if warn_lines:
                    report["status"] = "PARTIAL"
                    report["message"] = (
                        f"完成，累计 {total_upsert} 条，"
                        f"另有 {len(warn_lines)} 条告警/解析提示"
                    )
                    self.stdout.write(self.style.WARNING(report["message"]))
                else:
                    report["status"] = "SUCCESS"
                    report["message"] = f"完成，累计 {total_upsert} 条"
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
            report["message"] = str(exc)
            self.stderr.write(self.style.ERROR(f"执行失败: {exc}"))
            raise
        finally:
            if report.get("notify", True):
                self._send_result_email(report, started_at, timezone.now(), base_url)
