"""
交易日 08:40 拉取 fareport（FARPORT_MAIL_*）邮箱中主题
「0311020009225553上海吾执投资管理有限公司－吾执量化精选一号私募证券投资基金{YYYYMMDD}」
的最近 2 封邮件（同主题重复投递时可能各带附件），仅保存并解析直接附带的 .xlsx/.xls
（忽略 .zip，券商 zip 内 xlsx 常为加密压缩），合并写入 position_fund_real.LHJX。

持仓日 position_date 默认取运行日之前最近一个交易日（T-1），与主题末尾日期对齐。
证券代码统一为 code 字段：SZ002008、SH600660、BJ430047 等；position_size 为持有数量（股数）；
position_value 为市值；avg_price = 市值/数量（可空）。解析遇「人民币资金余额」「交易明细」「流水明细」即停止。

IMAP：`.env` 中 FARPORT_MAIL_USER / FARPORT_MAIL_PASS、ALPHA_IMAP_SERVER、ALPHA_IMAP_PORT。

业务约定：仅运行日为交易日时执行；非交易日不执行、不通知。成功/失败发 ALPHA_NOTIFY_* 结果邮件。

调度：alpha_mail_scheduler 默认 08:40（环境变量 LHJX_POSITION_MAIL_SCHEDULER_ENABLED）。

用法：
  python manage.py auto_import_lhjx_position_mail
  python manage.py auto_import_lhjx_position_mail --force
  python manage.py auto_import_lhjx_position_mail --position-date 2026-05-14
"""
from __future__ import annotations

import imaplib
import os
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.db.mongo import get_lhjx_position_collection
from portal.services.imap_common import find_recent_mail_ids_by_exact_subject
from portal.services.lhjx_position_mail_service import (
    build_lhjx_position_mail_subject,
    list_lhjx_position_xlsx_files,
    merge_lhjx_position_docs,
    parse_lhjx_position_excel,
)
from portal.services.mail_import_common import (
    emit_mail_job_result_line,
    format_mail_job_notify_body,
    imap_logout_safe,
    imap_open_inbox,
    mail_job_notify_base,
    save_excel_attachments_from_rfc822,
    send_alpha_notify_result_email,
    validate_mail_job_query_span,
)
from portal.services.trade_calendar_service import (
    is_trade_date_iso,
    prev_trading_day_iso_before,
)


def _resolve_farport_imap_credentials() -> tuple[str, str, str, int]:
    user = (os.getenv("FARPORT_MAIL_USER") or "").strip()
    pwd = (os.getenv("FARPORT_MAIL_PASS") or "").strip()
    host = (os.getenv("ALPHA_IMAP_SERVER") or "imap.exmail.qq.com").strip()
    port = int(os.getenv("ALPHA_IMAP_PORT") or "993")
    if not (user and pwd):
        raise RuntimeError(
            "未配置 FARPORT 邮箱，请在 .env 设置 FARPORT_MAIL_USER、FARPORT_MAIL_PASS。"
        )
    return user, pwd, host, port


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行：抓取 fareport 量化精选一号对账单最近 2 封同主题邮件，"
        "解析全部直接附带的 xlsx 持仓明细（忽略 zip）并写入 position_fund_real.LHJX；"
        "position_date 默认为运行日之前最近一个交易日。"
    )

    LHJX_MAIL_FETCH_LIMIT = 2

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
            f"[{status}] 量化精选一号 LHJX 持仓邮件导入 "
            f"{timezone.localdate().strftime('%Y-%m-%d')}"
        )
        title = "量化精选一号持仓（position_fund_real / LHJX）自动导入结果"
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
                ("匹配邮件数", report.get("mails_matched")),
                ("附件文件", report.get("source_file")),
                ("写入条数", report.get("rows_written")),
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
                "position_date": report.get("position_date") or "",
                "nav_date": report.get("position_date") or "",
                "target_subject": report.get("target_subject") or "",
                "source_file": report.get("source_file") or "",
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
            "mails_matched": 0,
            "source_file": "",
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
            target_subject = build_lhjx_position_mail_subject(ymd8)
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

            email_user, email_pass, imap_server, imap_port = _resolve_farport_imap_credentials()
            self.stdout.write(f"IMAP: {email_user} @ {imap_server}:{imap_port}")

            save_root = Path(settings.ALPHADATA_DIR) / "lhjx_position_mail" / ymd8
            mailbox: imaplib.IMAP4_SSL | None = None
            try:
                mailbox = imap_open_inbox(
                    email_user, email_pass, imap_server, imap_port
                )
                mail_ids = find_recent_mail_ids_by_exact_subject(
                    mailbox,
                    target_subject,
                    limit=self.LHJX_MAIL_FETCH_LIMIT,
                )
                report["mails_matched"] = len(mail_ids)
                if not mail_ids:
                    report["status"] = "FAILED"
                    report["message"] = "未找到匹配主题的邮件。"
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                self.stdout.write(
                    f"匹配主题邮件 {len(mail_ids)} 封（最多 {self.LHJX_MAIL_FETCH_LIMIT} 封）"
                )

                all_xlsx: list[Path] = []
                for idx, mail_id in enumerate(mail_ids, start=1):
                    st2, msg_data = mailbox.fetch(mail_id, "(RFC822)")
                    if st2 != "OK" or not msg_data or not msg_data[0]:
                        self.stderr.write(
                            self.style.WARNING(f"邮件 #{idx} ({mail_id}) 无法读取，跳过。")
                        )
                        continue
                    raw = msg_data[0][1]
                    if not isinstance(raw, (bytes, bytearray)):
                        self.stderr.write(
                            self.style.WARNING(f"邮件 #{idx} ({mail_id}) 内容格式异常，跳过。")
                        )
                        continue
                    mail_dir = save_root / f"mail_{idx}_{mail_id}"
                    excels = save_excel_attachments_from_rfc822(raw, mail_dir)
                    if excels:
                        all_xlsx.extend(excels)
                        self.stdout.write(
                            f"邮件 #{idx}: Excel 附件 {len(excels)} 个 -> "
                            + ", ".join(p.name for p in excels)
                        )
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f"邮件 #{idx} ({mail_id}) 无直接附带的 xlsx/xls，跳过（已忽略 zip）。"
                            )
                        )

                xlsx_files = list_lhjx_position_xlsx_files(all_xlsx)
                if not xlsx_files:
                    report["status"] = "FAILED"
                    report["message"] = (
                        f"共 {len(mail_ids)} 封邮件，未得到可导入的 Excel（.xlsx/.xls/.xlsm）。"
                    )
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                report["source_file"] = "; ".join(p.name for p in xlsx_files)
                doc_batches: list[list[dict]] = []
                # 低分文件先解析，高分（如 T_0003）后写入，合并时同 code 保留高分文件
                for fp in reversed(xlsx_files):
                    self.stdout.write(f"解析: {fp}")
                    batch = parse_lhjx_position_excel(
                        fp.read_bytes(),
                        filename=fp.name,
                        position_date_iso=pos_iso,
                    )
                    doc_batches.append(batch)
                    self.stdout.write(
                        f"  <- {fp.name}: {len(batch)} 条持仓"
                    )
                docs = merge_lhjx_position_docs(doc_batches)

                now = timezone.now()
                if isinstance(now, datetime) and timezone.is_naive(now):
                    now = timezone.make_aware(now, timezone.get_current_timezone())

                coll = get_lhjx_position_collection()
                coll.delete_many({"date": pos_iso})
                n = 0
                if docs:
                    for d in docs:
                        d["source_subject"] = target_subject
                        d["updated_at"] = now
                    coll.insert_many(docs)
                    n = len(docs)
                    try:
                        coll.create_index(
                            [("date", 1), ("code", 1)],
                            unique=True,
                            name="uniq_lhjx_date_code",
                        )
                    except Exception:
                        pass

                report["rows_written"] = n
                report["status"] = "SUCCESS"
                report["message"] = (
                    f"已写入 position_fund_real.LHJX 共 {n} 条（date={pos_iso}），"
                    f"来自 {len(mail_ids)} 封邮件、{len(xlsx_files)} 个 xlsx："
                    f"{report['source_file']}"
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
