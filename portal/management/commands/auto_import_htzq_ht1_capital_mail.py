"""
交易日早晨拉取「华泰证券江阴周庄镇西大街对账单吾执博士一号{YYYYMMDD}」邮件，
IMAP 使用 `.env` 中 FARPORT_MAIL_USER / FARPORT_MAIL_PASS 与 ALPHA_IMAP_SERVER / ALPHA_IMAP_PORT，
从附件 `666810103835_吾执博士一号_普通账单_HT1_*.xlsx` 首表「资金情况」解析一行数据，
写入 MongoDB：fund_nav_real.WZ_BSYH_HTQH_666810103835。

业务约定：运行日 D 为交易日时执行；非交易日不执行、不通知。对账单日期 statement_date
默认取「运行日之前最近一个交易日」= 与博士一号净值邮件相同的 T-1 口径。
调度：portal.scheduler.alpha_mail_scheduler 默认 09:00（环境变量 HTZQ_HT1_CAPITAL_MAIL_SCHEDULER_ENABLED）。

用法：
  python manage.py auto_import_htzq_ht1_capital_mail
  python manage.py auto_import_htzq_ht1_capital_mail --force
  python manage.py auto_import_htzq_ht1_capital_mail --statement-date 2026-04-21
"""
from __future__ import annotations

import imaplib
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.config.mail_imap import resolve_farport_imap_credentials
from portal.services.htzq_ht1_capital_service import (
    parse_ht1_capital_first_sheet,
    statement_ymd_from_filename,
    upsert_ht1_capital_doc,
)
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


TARGET_SUBJECT_PREFIX = "华泰证券江阴周庄镇西大街对账单吾执博士一号"


def _build_target_subject(statement_ymd: str) -> str:
    return f"{TARGET_SUBJECT_PREFIX}{statement_ymd}"


def _pick_plain_ht1_xlsx(files: list[Path], ymd: str) -> Path | None:
    """多附件时选取「普通账单_HT1」，忽略「证券理财账单」。"""
    exact_suffix = f"_吾执博士一号_普通账单_HT1_{ymd}.xlsx"
    for p in files:
        name = p.name
        if "证券理财账单" in name:
            continue
        if name.endswith(exact_suffix) or name.lower().endswith(exact_suffix.lower()):
            return p
    loose: list[Path] = []
    for p in files:
        name = p.name
        if "证券理财账单" in name:
            continue
        if "普通账单_HT1" in name and name.lower().endswith(".xlsx"):
            loose.append(p)
    return loose[0] if loose else None


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行：抓取华泰博士一号 HT1 普通账单 xlsx 并写入 "
        "fund_nav_real.WZ_BSYH_HTQH_666810103835；statement_date 默认为运行日之前最近一个交易日。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="忽略「运行日须为交易日」及 statement-date 的交易日校验，仍尝试导入。",
        )
        parser.add_argument(
            "--statement-date",
            default="",
            help="对账单日期 YYYY-MM-DD；默认取运行日之前最近一个交易日（本地时区）。",
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
            f"[{status}] 华泰 HT1 资金情况导入 "
            f"{timezone.localdate().strftime('%Y-%m-%d')}"
        )
        body = "\n".join(
            [
                "华泰 HT1 普通账单「资金情况」（fund_nav_real / WZ_BSYH_HTQH_666810103835）",
                "",
                f"状态: {status}",
                f"开始时间: {timezone.localtime(started_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"结束时间: {timezone.localtime(ended_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"运行时长(秒): {duration_sec}",
                f"服务地址: {base_url or '-'}",
                f"对账单日期(statement_date): {report.get('statement_date') or '-'}",
                f"运行日为交易日: {report.get('run_day_is_trading')}",
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
            "statement_date": "",
            "run_day_is_trading": False,
            "notify": True,
            "target_subject": "",
            "source_file": "",
            "message": "",
            "error": "",
        }

        local_date = timezone.localdate()
        run_iso = local_date.isoformat()
        report["run_day_is_trading"] = is_trade_date_iso(run_iso)

        self.stdout.write(
            f"运行日: {run_iso}，运行日是否交易日: {report['run_day_is_trading']}"
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

            stmt_raw = (options["statement_date"] or "").strip()
            if stmt_raw:
                stmt_iso = stmt_raw[:10]
            else:
                stmt_iso = prev_trading_day_iso_before(run_iso) or ""
                if not stmt_iso:
                    report["status"] = "FAILED"
                    report["message"] = (
                        "无法在 trade_calendar 中解析「运行日之前最近一个交易日」，"
                        "请检查日历数据是否已导入。"
                    )
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

            report["statement_date"] = stmt_iso
            stmt_is_trading = is_trade_date_iso(stmt_iso)
            ymd = stmt_iso.replace("-", "")
            target_subject = _build_target_subject(ymd)
            report["target_subject"] = target_subject

            self.stdout.write(f"目标对账单日期(statement_date): {stmt_iso}")

            span_err = validate_mail_job_query_span(
                query_iso=stmt_iso,
                run_iso=run_iso,
                force=bool(options["force"]),
            )
            if span_err:
                report["status"] = "FAILED"
                report["message"] = span_err
                self.stderr.write(self.style.ERROR(span_err))
                return

            if stmt_raw and not options["force"] and not stmt_is_trading:
                report["status"] = "SKIPPED"
                report["message"] = (
                    f"{stmt_iso} 非交易日（trade_calendar），跳过。"
                    "使用 --force 可强制执行。"
                )
                self.stdout.write(self.style.WARNING(report["message"]))
                return

            email_user, email_pass, imap_server, imap_port = (
                resolve_farport_imap_credentials()
            )
            self.stdout.write(f"IMAP: {email_user} @ {imap_server}:{imap_port}")

            save_root = (
                Path(settings.ALPHADATA_DIR)
                / "htzq_ht1_capital_mail"
                / ymd
            )

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

                fp = _pick_plain_ht1_xlsx(files, ymd)
                if not fp:
                    report["status"] = "FAILED"
                    report["message"] = (
                        "未找到「普通账单_HT1」xlsx（已忽略证券理财账单）。"
                    )
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                report["source_file"] = fp.name
                data = fp.read_bytes()
                file_ymd = statement_ymd_from_filename(fp.name)
                if file_ymd and file_ymd != ymd:
                    raise ValueError(
                        f"附件文件名日期 {file_ymd} 与目标对账单日 {ymd} 不一致"
                    )

                doc = parse_ht1_capital_first_sheet(
                    data,
                    filename=fp.name,
                    expected_statement_iso=stmt_iso,
                )
                upsert_ht1_capital_doc(
                    doc,
                    source_subject=target_subject,
                    source_file=fp.name,
                )
                report["status"] = "SUCCESS"
                report["message"] = (
                    f"已落库 statement_date={doc['statement_date']} <- {fp.name}"
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
