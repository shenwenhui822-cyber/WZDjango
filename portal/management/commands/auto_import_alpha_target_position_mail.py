"""
交易日 11:30 从 FTP /new_holding 拉取各子目录当日 YYYYMMDD.csv，
写入 position_alpha_target.<目录名>（字段 date、ticker、lots、updated_at）。

FTP 默认：192.168.110.199/new_holding（wuzhi199 / wuzhi2026），
可用环境变量 ALPHA_TARGET_FTP_* 覆盖。

持仓日 position_date 默认取运行日本地日期。
业务约定：仅运行日为交易日时执行；非交易日不执行、不通知。

调度：alpha_mail_scheduler 默认 11:30（ALPHA_TARGET_POSITION_MAIL_SCHEDULER_ENABLED）。

用法：
  python manage.py auto_import_alpha_target_position_mail
  python manage.py auto_import_alpha_target_position_mail --force
  python manage.py auto_import_alpha_target_position_mail --position-date 2026-05-15
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.services.alpha_target_position_ftp_service import import_alpha_target_from_ftp
from portal.services.mail_import_common import (
    emit_mail_job_result_line,
    format_mail_job_notify_body,
    mail_job_notify_base,
    send_alpha_notify_result_email,
    validate_mail_job_query_span,
)
from portal.services.trade_calendar_service import is_trade_date_iso


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行：从 FTP /new_holding 拉取当日 CSV，"
        "写入 position_alpha_target；position_date 默认为运行日。"
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
            f"[{status}] Alpha 目标持仓 FTP 导入 "
            f"{timezone.localdate().strftime('%Y-%m-%d')}"
        )
        title = "Alpha 目标持仓（position_alpha_target）FTP 导入结果"
        imported = report.get("imported") or []
        detail_lines = []
        for row in imported:
            if isinstance(row, dict):
                detail_lines.append(
                    f"- {row.get('table')}: {row.get('rows')} 条 <- {row.get('file')}"
                )
        missing = report.get("folders_missing") or []
        data_ok = status == "SUCCESS"
        body = format_mail_job_notify_body(
            title=title,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            base_url=base_url,
            field_rows=[
                ("持仓日(position_date)", report.get("position_date")),
                ("FTP 主机", report.get("ftp_host")),
                ("FTP 目录", report.get("remote_dir")),
                ("目标 CSV", report.get("csv_name")),
                ("目录总数", report.get("folders_found")),
                ("写入总条数", report.get("rows_written")),
                ("缺失 CSV 的目录", ", ".join(missing) if missing else "无"),
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
                "remote_dir": report.get("remote_dir") or "",
                "rows_written": report.get("rows_written", 0),
                "folders_found": report.get("folders_found", 0),
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
            "ftp_host": getattr(settings, "ALPHA_TARGET_FTP_HOST", ""),
            "remote_dir": getattr(settings, "ALPHA_TARGET_FTP_REMOTE_DIR", ""),
            "csv_name": "",
            "folders_found": 0,
            "folders_missing": [],
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
            pos_iso = pos_raw[:10] if pos_raw else run_iso
            report["position_date"] = pos_iso
            ymd8 = pos_iso.replace("-", "")
            report["csv_name"] = f"{ymd8}.csv"

            self.stdout.write(f"持仓日期(position_date): {pos_iso}")
            self.stdout.write(
                f"FTP: {report['ftp_host']}{report['remote_dir']}/{report['csv_name']}"
            )

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

            ftp_result = import_alpha_target_from_ftp(date_iso=pos_iso)
            report["status"] = ftp_result.status
            report["remote_dir"] = ftp_result.remote_dir
            report["csv_name"] = ftp_result.csv_name
            report["folders_found"] = ftp_result.folders_found
            report["folders_missing"] = ftp_result.folders_missing
            report["rows_written"] = ftp_result.rows_written
            report["imported"] = ftp_result.folders_imported
            report["message"] = ftp_result.message
            report["error"] = ftp_result.error

            for row in ftp_result.folders_imported:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"position_alpha_target.{row['table']}: "
                        f"{row['rows']} 条（date={pos_iso}）<- {row['file']}"
                    )
                )
            for table in ftp_result.folders_missing:
                self.stdout.write(
                    self.style.WARNING(
                        f"跳过 {table}：无 {ftp_result.csv_name}"
                    )
                )

            if ftp_result.status == "SUCCESS":
                self.stdout.write(self.style.SUCCESS(ftp_result.message))
            elif ftp_result.error:
                self.stderr.write(self.style.ERROR(ftp_result.message))
                raise RuntimeError(ftp_result.error)
            else:
                self.stderr.write(self.style.ERROR(ftp_result.message))

        except Exception as exc:
            report["status"] = "FAILED"
            report["error"] = str(exc)
            report["message"] = str(exc)
            self.stderr.write(self.style.ERROR(f"执行失败: {exc}"))
            raise
        finally:
            if report.get("notify", True):
                self._send_result_email(report, started_at, timezone.now(), base_url)
