"""
交易日 09:20 拉取 wangkan（ALPHA_MAIL_*）邮箱中主题
「股票持仓数据{YYYYMMDD}」的邮件，下载附件 alpha_{YYYYMMDD}.zip，
解压 CSV（ticker、lots）写入 position_alpha_target.<表名>（表名 = csv 文件名去后缀）。

持仓日 position_date 默认取运行日本地日期（与主题末尾 YYYYMMDD 一致）。
IMAP：`.env` 中 ALPHA_MAIL_USER / ALPHA_MAIL_PASS、ALPHA_IMAP_SERVER、ALPHA_IMAP_PORT。

业务约定：仅运行日为交易日时执行；非交易日不执行、不通知。成功/失败发 ALPHA_NOTIFY_* 结果邮件。

调度：alpha_mail_scheduler 默认 09:20（环境变量 ALPHA_TARGET_POSITION_MAIL_SCHEDULER_ENABLED）。

用法：
  python manage.py auto_import_alpha_target_position_mail
  python manage.py auto_import_alpha_target_position_mail --force
  python manage.py auto_import_alpha_target_position_mail --position-date 2026-05-15
"""
from __future__ import annotations

import imaplib
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.config.mail_imap import resolve_imap_credentials
from portal.db.mongo import get_alpha_target_position_collection
from portal.services.alpha_target_position_mail_service import (
    build_alpha_target_position_subject,
    collection_name_from_csv,
    list_csv_in_extract_dir,
    parse_alpha_target_csv,
    pick_alpha_target_zip,
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
from portal.services.trade_calendar_service import is_trade_date_iso


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行：抓取「股票持仓数据」邮件 zip 附件，"
        "解析 CSV 写入 position_alpha_target；position_date 默认为运行日。"
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
            help="持仓日 YYYY-MM-DD；默认取运行日本地日期。",
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
            f"[{status}] Alpha 目标持仓邮件导入 "
            f"{timezone.localdate().strftime('%Y-%m-%d')}"
        )
        title = "Alpha 目标持仓（position_alpha_target）自动导入结果"
        imported = report.get("imported") or []
        detail_lines = []
        for row in imported:
            if isinstance(row, dict):
                detail_lines.append(
                    f"- {row.get('table')}: {row.get('rows')} 条 <- {row.get('file')}"
                )
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
                ("zip 附件", report.get("source_zip")),
                ("CSV 文件数", report.get("csv_count")),
                ("写入总条数", report.get("rows_written")),
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
                "source_zip": report.get("source_zip") or "",
                "rows_written": report.get("rows_written", 0),
                "csv_count": report.get("csv_count", 0),
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
            "source_zip": "",
            "csv_count": 0,
            "rows_written": 0,
            "imported": [],
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
                pos_iso = run_iso

            report["position_date"] = pos_iso
            ymd8 = pos_iso.replace("-", "")
            target_subject = build_alpha_target_position_subject(ymd8)
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

            if pos_raw and not options["force"] and not is_trade_date_iso(pos_iso):
                report["status"] = "SKIPPED"
                report["message"] = (
                    f"{pos_iso} 非交易日（trade_calendar），跳过。"
                    "使用 --force 可强制执行。"
                )
                self.stdout.write(self.style.WARNING(report["message"]))
                return

            email_user, email_pass, imap_server, imap_port = resolve_imap_credentials()
            self.stdout.write(f"IMAP: {email_user} @ {imap_server}:{imap_port}")

            save_root = Path(settings.ALPHADATA_DIR) / "alpha_target_position_mail" / ymd8
            mailbox: imaplib.IMAP4_SSL | None = None
            try:
                mailbox = imap_open_inbox(
                    email_user, email_pass, imap_server, imap_port
                )
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

                zips = save_zip_attachments_from_rfc822(raw, save_root)
                if not zips:
                    report["status"] = "FAILED"
                    report["message"] = "邮件中无 .zip 附件。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                zip_path = pick_alpha_target_zip(zips, ymd8)
                if not zip_path:
                    report["status"] = "FAILED"
                    report["message"] = "未选择到 zip 附件。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                report["source_zip"] = zip_path.name
                extract_dir = save_root / "_zip_extract" / zip_path.stem
                extract_zip_archive(zip_path, extract_dir)
                csv_files = list_csv_in_extract_dir(extract_dir)
                if not csv_files:
                    report["status"] = "FAILED"
                    report["message"] = f"zip 内未找到有效 CSV（解压目录: {extract_dir}）。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                report["csv_count"] = len(csv_files)
                now = timezone.now()
                if isinstance(now, datetime) and timezone.is_naive(now):
                    now = timezone.make_aware(now, timezone.get_current_timezone())

                imported_stats: list[dict] = []
                total_rows = 0
                for fp in csv_files:
                    table = collection_name_from_csv(fp)
                    if not table:
                        self.stdout.write(self.style.WARNING(f"跳过非法表名: {fp.name}"))
                        continue
                    docs = parse_alpha_target_csv(fp, date_iso=pos_iso)
                    for d in docs:
                        d["source_file"] = fp.name
                        d["source_subject"] = target_subject
                        d["updated_at"] = now

                    coll = get_alpha_target_position_collection(table)
                    coll.delete_many({"date": pos_iso})
                    n = 0
                    if docs:
                        coll.insert_many(docs)
                        n = len(docs)
                        try:
                            coll.create_index(
                                [("date", 1), ("ticker", 1)],
                                unique=True,
                                name=f"uniq_{table}_date_ticker",
                            )
                        except Exception:
                            pass
                    total_rows += n
                    imported_stats.append(
                        {"table": table, "file": fp.name, "rows": n}
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"position_alpha_target.{table}: {n} 条（date={pos_iso}）<- {fp.name}"
                        )
                    )

                if not imported_stats:
                    report["status"] = "FAILED"
                    report["message"] = "未写入任何表（CSV 表名均无效或为空）。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                report["imported"] = imported_stats
                report["rows_written"] = total_rows
                report["status"] = "SUCCESS"
                report["message"] = (
                    f"已写入 position_alpha_target 共 {len(imported_stats)} 张表、"
                    f"{total_rows} 条（date={pos_iso}）<- {zip_path.name}"
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
