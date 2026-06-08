"""
交易日 11:20 拉取 wangkan（ALPHA_MAIL_*）邮箱中主题「吾执 YYYY-MM-DD」
的邮件，保存 zip 附件并解压，将内层 CSV 写入 position_alpha_source.<表名>。

字段：market（交易市场）、ticker（证券代码）、algo_weight（算法数量/权重）、
t0_qty（T0数量）、date。

持仓日 position_date 默认取运行日之前最近一个交易日（T-1），与邮件主题日期对齐。

IMAP：`.env` 中 ALPHA_MAIL_USER / ALPHA_MAIL_PASS、ALPHA_IMAP_SERVER、ALPHA_IMAP_PORT。
业务约定：仅运行日为交易日时执行；非交易日不执行、不通知。成功/失败发 ALPHA_NOTIFY_* 结果邮件。

调度：alpha_mail_scheduler 默认 11:20（ALPHA_SOURCE_POSITION_MAIL_SCHEDULER_ENABLED）。

用法：
  python manage.py auto_import_alpha_source_position_mail
  python manage.py auto_import_alpha_source_position_mail --force
  python manage.py auto_import_alpha_source_position_mail --position-date 2026-06-05
"""
from __future__ import annotations

import imaplib
import shutil
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.config.mail_imap import resolve_imap_credentials
from portal.services.alpha_source_position_mail_service import (
    build_alpha_source_mail_subject,
    import_alpha_source_from_csv_paths,
)
from portal.services.imap_common import find_latest_mail_id_by_exact_subject
from portal.services.mail_import_common import (
    emit_mail_job_result_line,
    extract_zip_archive,
    format_mail_job_notify_body,
    imap_logout_safe,
    imap_open_inbox,
    mail_job_notify_base,
    save_zip_attachments_from_rfc822,
    send_alpha_notify_result_email,
    validate_mail_job_query_span,
)
from portal.services.trade_calendar_service import (
    is_trade_date_iso,
    prev_trading_day_iso_before,
)


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行：抓取 wangkan 吾执 zip 持仓邮件，"
        "解压 CSV 并写入 position_alpha_source；"
        "position_date 默认为运行日之前最近一个交易日。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="忽略「运行日须为交易日」及 position-date 的交易日校验，仍尝试导入。",
        )
        parser.add_argument(
            "--position-date",
            default="",
            help="持仓日 YYYY-MM-DD；默认取运行日之前最近一个交易日（本地时区）。",
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
            f"[{status}] Alpha 源持仓 position_alpha_source 邮件导入 "
            f"{timezone.localdate().strftime('%Y-%m-%d')}"
        )
        title = "Alpha 源持仓（position_alpha_source）邮件自动导入结果"
        imported = report.get("imported") or []
        detail_lines = []
        for row in imported:
            if isinstance(row, dict):
                detail_lines.append(
                    f"- {row.get('table')}: {row.get('rows')} 条 <- {row.get('file')}"
                )
        unmapped = report.get("files_unmapped") or []
        data_ok = status == "SUCCESS"
        body = format_mail_job_notify_body(
            title=title,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            base_url=base_url,
            field_rows=[
                ("持仓日(position_date)", report.get("position_date")),
                ("邮件主题", report.get("target_subject")),
                ("zip 附件", report.get("zip_file")),
                ("写入总条数", report.get("rows_written")),
                ("未映射 CSV", ", ".join(unmapped) if unmapped else "无"),
                ("结果说明", report.get("message")),
                ("异常信息", report.get("error")),
            ],
            extra_sections=[
                "\n".join(["各表写入:", *(detail_lines or ["- 无"])])
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
                "position_date": report.get("position_date") or "",
                "nav_date": report.get("position_date") or "",
                "target_subject": report.get("target_subject") or "",
                "source_file": report.get("zip_file") or "",
                "rows_written": report.get("rows_written", 0),
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
            "position_date": "",
            "notify": True,
            "target_subject": "",
            "zip_file": "",
            "imported": [],
            "files_unmapped": [],
            "rows_written": 0,
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

            pos_raw = (options["position_date"] or "").strip()
            if pos_raw:
                pos_iso = pos_raw[:10]
            else:
                pos_iso = prev_trading_day_iso_before(run_iso) or ""
                if not pos_iso:
                    report["status"] = "FAILED"
                    report["message"] = (
                        "无法在 trade_calendar 中解析「运行日之前最近一个交易日」。"
                    )
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

            report["position_date"] = pos_iso
            pos_td = is_trade_date_iso(pos_iso)
            ymd8 = pos_iso.replace("-", "")
            target_subject = build_alpha_source_mail_subject(pos_iso)
            report["target_subject"] = target_subject

            self.stdout.write(f"持仓日期(position_date): {pos_iso}")
            self.stdout.write(f"目标主题: {target_subject}")

            span_err = validate_mail_job_query_span(
                query_iso=pos_iso,
                run_iso=run_iso,
                force=bool(options["force"]),
            )
            if span_err:
                report["status"] = "FAILED"
                report["message"] = span_err
                self.stderr.write(self.style.ERROR(span_err))
                return

            if pos_raw and not options["force"] and not pos_td:
                report["status"] = "SKIPPED"
                report["message"] = (
                    f"{pos_iso} 非交易日（trade_calendar），跳过。"
                    "使用 --force 可强制执行。"
                )
                self.stdout.write(self.style.WARNING(report["message"]))
                return

            email_user, email_pass, imap_server, imap_port = resolve_imap_credentials()
            self.stdout.write(f"IMAP: {email_user} @ {imap_server}:{imap_port}")

            save_root = Path(settings.ALPHADATA_DIR) / "alpha_source_position_mail" / ymd8
            extract_dir = save_root / "_zip_extract"
            since_day = date.fromisoformat(pos_iso) - timedelta(days=7)

            mailbox: imaplib.IMAP4_SSL | None = None
            try:
                mailbox = imap_open_inbox(
                    email_user, email_pass, imap_server, imap_port
                )
                mail_id = find_latest_mail_id_by_exact_subject(
                    mailbox,
                    target_subject,
                    since_calendar_date=since_day,
                )
                if not mail_id:
                    report["status"] = "FAILED"
                    report["message"] = f"未找到匹配主题的邮件: {target_subject!r}"
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

                zip_files = save_zip_attachments_from_rfc822(raw, save_root)
                if not zip_files:
                    report["status"] = "FAILED"
                    report["message"] = "邮件中无 zip 附件。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                zip_path = zip_files[0]
                report["zip_file"] = zip_path.name
                if extract_dir.exists():
                    shutil.rmtree(extract_dir, ignore_errors=True)
                extract_dir.mkdir(parents=True, exist_ok=True)
                extract_zip_archive(zip_path, extract_dir)

                csv_paths = sorted(
                    p
                    for p in extract_dir.rglob("*.csv")
                    if "__MACOSX" not in p.parts and not p.name.startswith("._")
                )
                imp = import_alpha_source_from_csv_paths(
                    csv_paths,
                    position_date_iso=pos_iso,
                    source_subject=target_subject,
                )
                report["status"] = imp.status
                report["imported"] = imp.imported
                report["files_unmapped"] = imp.files_unmapped
                report["rows_written"] = imp.rows_written
                report["message"] = imp.message
                report["error"] = imp.error

                if imp.status == "SUCCESS":
                    self.stdout.write(self.style.SUCCESS(imp.message))
                else:
                    self.stderr.write(self.style.ERROR(imp.message))
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
