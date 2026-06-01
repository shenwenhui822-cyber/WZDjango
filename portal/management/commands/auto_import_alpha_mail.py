"""
交易日自动拉取 alpha 日报邮件（主题 alpha产品日报表YYYYMMDD）中的 xlsx 并导入 MongoDB。

设计为每个交易日 11:00 由系统计划任务执行（仅运行日为交易日时拉取并导入；非交易日不执行、不发结果邮件）：
    python manage.py auto_import_alpha_mail

默认主题日期为运行日之前最近一个交易日；邮件内仅处理以下两种 xlsx 附件：
  - Alpha产品表现汇总_{YYYYMMDD}.xlsx（原表，按既有规则规范化产品名）
  - 新Alpha产品表现汇总_{YYYYMMDD}.xlsx（新表，所有产品名加「产品-」前缀）

邮箱：见 portal.config.mail_imap（.env / 环境变量）。
"""
from __future__ import annotations

import email
import imaplib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from portal.config.mail_imap import resolve_imap_credentials
from portal.data.alpha_daily_schema import (
    alpha_daily_mail_attachment_pattern_legacy,
    alpha_daily_mail_attachment_pattern_new,
    classify_alpha_daily_mail_attachment,
    normalize_alpha_daily_product_name_for_import,
    normalize_alpha_daily_product_name_for_new_summary_import,
)
from portal.db.mongo import get_trade_date_collection
from portal.services.imap_common import (
    decode_mime_header,
    find_latest_mail_id_by_exact_subject,
    normalize_attachment_filename,
)
from portal.services.import_service import import_excel_fileobj
from portal.services.mail_import_common import (
    emit_mail_job_result_line,
    format_mail_job_notify_body,
    imap_logout_safe,
    imap_open_inbox,
    iso_date_from_yyyymmdd,
    mail_job_notify_base,
    send_alpha_notify_result_email,
    validate_mail_job_query_span,
)
from portal.services.trade_calendar_service import prev_trading_day_iso_before


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行：抓取 alpha 日报邮件 xlsx 并导入 MongoDB；"
        "默认主题日期为上一交易日；非交易日不执行且不发送结果邮件（建议每个交易日 11:00 计划任务）"
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
            help="邮件主题日期 YYYYMMDD，默认取运行日之前最近一个交易日。",
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
        run_iso = local_date.isoformat()
        subject_date = (options["subject_date"] or "").strip()
        if not subject_date:
            prev_iso = prev_trading_day_iso_before(run_iso) or ""
            if not prev_iso:
                report["status"] = "FAILED"
                report["message"] = (
                    "无法在 trade_calendar 中解析「运行日之前最近一个交易日」。"
                )
                self.stderr.write(self.style.ERROR(report["message"]))
                raise CommandError(report["message"])
            subject_date = prev_iso.replace("-", "")
        report["subject_date"] = subject_date

        try:
            query_iso = iso_date_from_yyyymmdd(subject_date)
            span_err = validate_mail_job_query_span(
                query_iso=query_iso,
                run_iso=run_iso,
                force=bool(options["force"]),
            )
            if span_err:
                report["status"] = "FAILED"
                report["message"] = span_err
                self.stderr.write(self.style.ERROR(span_err))
                raise CommandError(span_err)

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
                mailbox = imap_open_inbox(
                    email_user, email_pass, imap_server, imap_port
                )

                mail_id = find_latest_mail_id_by_exact_subject(mailbox, target_subject)
                if not mail_id:
                    report["status"] = "NO_MAIL"
                    report["message"] = "未找到目标邮件。"
                    self.stdout.write(self.style.WARNING(report["message"]))
                    raise CommandError(report["message"])
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
                    raise CommandError(report["message"])

                imported_stats: list[dict] = []
                for fp in files:
                    kind = classify_alpha_daily_mail_attachment(fp.name, subject_date)
                    if kind == "new":
                        name_normalizer = (
                            normalize_alpha_daily_product_name_for_new_summary_import
                        )
                        import_mode = "new_summary"
                    else:
                        name_normalizer = normalize_alpha_daily_product_name_for_import
                        import_mode = "legacy"
                    with fp.open("rb") as f:
                        stat = import_excel_fileobj(
                            f,
                            fp.name,
                            alpha_daily_product_name_normalizer=name_normalizer,
                        )
                    imported_stats.append(
                        {
                            "file": fp.name,
                            "import_mode": import_mode,
                            "inserted": stat.get("inserted"),
                            "updated": stat.get("updated"),
                            "sheets": stat.get("sheets"),
                        }
                    )
                    self.stdout.write(
                        f"导入成功({import_mode}): {fp.name} -> "
                        f"inserted={stat.get('inserted')}, updated={stat.get('updated')}"
                    )
                report["imported"] = imported_stats
                report["status"] = "SUCCESS"
                report["message"] = f"完成: 共 {len(files)} 个 xlsx 已导入 MongoDB。"
                self.stdout.write(self.style.SUCCESS(report["message"]))
            finally:
                imap_logout_safe(mailbox)
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
        saved_by_kind: dict[str, Path] = {}
        legacy_pattern = alpha_daily_mail_attachment_pattern_legacy(subject_date)
        new_pattern = alpha_daily_mail_attachment_pattern_new(subject_date)
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
            if not (legacy_pattern.match(filename) or new_pattern.match(filename)):
                self.stdout.write(f"跳过非目标附件: {filename}")
                continue
            kind = classify_alpha_daily_mail_attachment(filename, subject_date)
            if not kind:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            output = save_dir / filename
            output.write_bytes(payload)
            saved_by_kind[kind] = output
            self.stdout.write(f"已保存附件({kind}): {output}")
        return list(saved_by_kind.values())

    def _send_result_email(
        self,
        report: dict[str, object],
        started_at,
        ended_at,
        base_url: str,
    ) -> None:
        status = str(report.get("status") or "UNKNOWN")
        mail_subject = (
            f"[{status}] Alpha 日报自动导入 {timezone.localdate().strftime('%Y-%m-%d')}"
        )
        imported = report.get("imported") or []
        imported_lines = []
        for row in imported:
            if isinstance(row, dict):
                imported_lines.append(
                    f"- {row.get('file')} ({row.get('import_mode')}): "
                    f"inserted={row.get('inserted')}, updated={row.get('updated')}"
                )
        title = "Alpha 日报自动导入执行结果"
        data_ok = status == "SUCCESS"
        body = format_mail_job_notify_body(
            title=title,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            base_url=base_url,
            field_rows=[
                ("交易日", report.get("is_trading_day")),
                ("主题日期", report.get("subject_date")),
                ("目标主题", report.get("target_subject")),
                ("命中邮件", report.get("mail_found")),
                ("下载附件数", len(report.get("saved_files") or [])),
                ("导入文件数", len(imported)),
                ("结果说明", report.get("message")),
                ("异常信息", report.get("error")),
            ],
            extra_sections=["\n".join(["导入明细:", *(imported_lines or ["- 无"])])],
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
                "is_trading_day": report.get("is_trading_day"),
                "subject_date": report.get("subject_date") or "",
                "target_subject": report.get("target_subject") or "",
                "mail_found": report.get("mail_found"),
                "saved_files_count": len(report.get("saved_files") or []),
                "imported_files_count": len(imported),
                "imported_detail": "\n".join(imported_lines) if imported_lines else "",
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
