"""
每交易日 15:45 触发：从 tradelog 各集合取当日 15:28 后最后一次落库，写入 position_close_record（同名集合，snapshot_date 不重复）。

用法：
  python manage.py sync_position_close_record
  python manage.py sync_position_close_record --force
  python manage.py sync_position_close_record --trade-date 2026-05-29
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from urllib.parse import urlparse

from portal.services.mail_import_common import (
    emit_mail_job_result_line,
    format_mail_job_notify_body,
    mail_job_notify_base,
    send_alpha_notify_result_email,
)
from portal.services.position_close_record_service import sync_position_close_for_date
from portal.services.trade_calendar_service import is_trade_date_iso


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行（定时 15:45）：从 tradelog 同步各账户当日 15:28 后最后一次落库至 "
        "position_close_record（集合名相同，按 snapshot_date upsert 不重复，范围见 ACCOUNT_BRIEF_DISPLAY_ORDER）。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="忽略「运行日须为交易日」校验。",
        )
        parser.add_argument(
            "--trade-date",
            default="",
            help="业务日 YYYY-MM-DD；默认为运行日（本地时区）。",
        )

    def _send_result_email(
        self,
        report: dict[str, object],
        started_at,
        ended_at,
        base_url: str,
    ) -> None:
        status = str(report.get("status") or "UNKNOWN")
        trade_date = report.get("trade_date") or ""
        mail_subject = (
            f"[{status}] tradelog 收盘快照同步 position_close_record "
            f"{trade_date or timezone.localdate()}"
        )
        title = "tradelog → position_close_record 账户收盘快照同步"
        data_ok = status == "SUCCESS"
        synced = report.get("synced", 0)
        missing = report.get("missing", 0)
        errors = report.get("errors", 0)
        total = report.get("total_collections", 0)
        body = format_mail_job_notify_body(
            title=title,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            base_url=base_url,
            field_rows=[
                ("业务日", trade_date),
                ("运行日为交易日", report.get("run_day_is_trading")),
                ("集合总数", total),
                ("写入成功", synced),
                ("当日无数据", missing),
                ("失败", errors),
                ("结果说明", report.get("message")),
                ("异常信息", report.get("error")),
            ],
            extra_sections=[str(report.get("detail_text") or "")],
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
                "target_subject": title,
                "trade_date": trade_date,
                "synced": synced,
                "missing": missing,
                "errors": errors,
                "total_collections": total,
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
            "trade_date": "",
            "run_day_is_trading": False,
            "notify": True,
            "total_collections": 0,
            "synced": 0,
            "missing": 0,
            "errors": 0,
            "message": "",
            "error": "",
            "detail_text": "",
        }

        run_iso = timezone.localdate().isoformat()
        report["run_day_is_trading"] = is_trade_date_iso(run_iso)
        trade_date = (options.get("trade_date") or "").strip()[:10] or run_iso
        report["trade_date"] = trade_date

        self.stdout.write(
            f"运行日: {run_iso}，是否交易日: {report['run_day_is_trading']}，"
            f"同步业务日: {trade_date}"
        )
        uri = getattr(settings, "MONGODB_URI", "") or ""
        parsed = urlparse(uri)
        mongo_host = parsed.hostname or uri or "—"
        tradelog_db = getattr(settings, "MONGODB_TRADELOG_DB", "tradelog")
        self.stdout.write(
            f"MongoDB: {mongo_host} / 库 {tradelog_db} "
            f"（取各集合 {trade_date} 15:28 后最后一次落库，按 snapshot_date upsert）"
        )

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

            result = sync_position_close_for_date(trade_date)
            report["total_collections"] = result["total_collections"]
            report["synced"] = result["synced"]
            report["missing"] = result["missing"]
            report["errors"] = result["errors"]

            lines = [
                f"{d['strategy_tag']}: {d['status']} — {d.get('message', '')}"
                for d in result.get("details") or []
            ]
            report["detail_text"] = "\n".join(lines)

            total = result["total_collections"]
            synced = result["synced"]
            missing = result["missing"]
            errors = result["errors"]
            if errors > 0:
                report["status"] = "FAILURE"
                report["message"] = (
                    f"未全部账户同步成功：写入 {synced}/{total} 个集合，"
                    f"无数据 {missing} 个，失败 {errors} 个。"
                )
            elif synced == 0:
                report["status"] = "FAILURE"
                report["message"] = f"{trade_date} 未写入任何账户快照。"
            elif missing > 0 or synced < total:
                report["status"] = "FAILURE"
                report["message"] = (
                    f"未全部账户同步成功：写入 {synced}/{total} 个集合，"
                    f"无数据 {missing} 个，失败 {errors} 个。"
                )
            else:
                report["status"] = "SUCCESS"
                report["message"] = f"完成：全部 {total} 个集合已写入。"

            if report["status"] == "SUCCESS":
                self.stdout.write(self.style.SUCCESS(report["message"]))
            else:
                self.stdout.write(self.style.WARNING(report["message"]))
            status_by_tag = {
                str(d.get("strategy_tag") or ""): d.get("status")
                for d in result.get("details") or []
            }
            for line in lines:
                tag = line.split(":", 1)[0]
                if status_by_tag.get(tag) in ("missing", "error"):
                    self.stdout.write(self.style.WARNING(line))
                else:
                    self.stdout.write(line)
        except Exception as exc:
            report["status"] = "FAILURE"
            report["error"] = str(exc)
            report["message"] = "同步异常终止。"
            self.stderr.write(self.style.ERROR(str(exc)))
            raise
        finally:
            if report.get("notify"):
                self._send_result_email(report, started_at, timezone.now(), base_url)
