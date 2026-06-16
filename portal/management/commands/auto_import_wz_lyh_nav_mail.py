"""
交易日 17:10 拉取 wangkan（ALPHA_MAIL_*）邮箱中主题
「【净值表】上海吾执投资管理有限公司…等6个产品净值表发送{YYYYMMDD}」
（YYYYMMDD 为 nav_date）的邮件，从附件「集合计划每日净值表」等 xls/xlsx 解析并落库：
  · 吾执零一号 STZ049 → fund_nav_real.WZ_LYH_MASTER
  · 吾执量化精选一号 SASQ16 → fund_nav_real.WZ_LHJXYH_MASTER

IMAP：`.env` 中 ALPHA_MAIL_USER / ALPHA_MAIL_PASS、ALPHA_IMAP_SERVER、ALPHA_IMAP_PORT。

业务约定：仅运行日为交易日时执行；非交易日不执行、不通知。nav_date 默认取运行日之前
最近一个交易日（T-1）。IMAP 取「与 nav_date 同一日历日」的 INTERNALDATE 且主题匹配的邮件（按交易日净值日对齐，非运行日前一自然日）。
STZ049 与 SASQ16 均成功 upsert 后停止继续遍历后续邮件。

调度：alpha_mail_scheduler 默认 17:10（环境变量 WZ_LYH_NAV_MAIL_SCHEDULER_ENABLED）。

用法：
  python manage.py auto_import_wz_lyh_nav_mail
  python manage.py auto_import_wz_lyh_nav_mail --force
  python manage.py auto_import_wz_lyh_nav_mail --nav-date 2026-05-11
"""
from __future__ import annotations

import imaplib
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.config.mail_imap import resolve_imap_credentials
from portal.data.fund_nav_real_config import FundNavProduct
from portal.services.fund_nav_real_service import parse_fund_nav_excel, upsert_fund_nav_doc
from portal.services.imap_common import (
    find_latest_mail_id_by_exact_subject,
    list_mail_ids_on_internal_calendar_day_matching_subjects,
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
from portal.services.wz_lyh_fund_nav_mail_service import (
    build_wz_lyh_nav_mail_subject_variants,
    get_wz_lyh_nav_mail_bundle_funds,
)


def _score_wz_lyh_nav_attachment(path: Path) -> int:
    """同批附件中优先「集合计划每日净值表」。"""
    name = path.name
    score = 0
    if "集合计划每日净值表" in name:
        score += 5
    if "净值表" in name:
        score += 2
    if "STZ049" in name or "SASQ16" in name:
        score += 1
    return score


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行：抓取管理人「等6个产品净值表发送YYYYMMDD」邮件附件，"
        "解析零一号 STZ049 与量化精选一号 SASQ16 两行并分别写入 "
        "WZ_LYH_MASTER、WZ_LHJXYH_MASTER；nav_date 默认为运行日之前最近一交易日；"
        "IMAP 按 nav_date 当日 INTERNALDATE 筛信；两产品均落库后即停止遍历邮件。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="忽略「运行日须为交易日」及 nav-date 的交易日校验，仍尝试导入。",
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
        status = str(report.get("status") or "UNKNOWN")
        mail_subject = (
            f"[{status}] 管理人净值表邮件导入（零一号+量化精选一号） "
            f"{timezone.localdate().strftime('%Y-%m-%d')}"
        )
        title = (
            "零一号 STZ049（WZ_LYH_MASTER）与量化精选一号 SASQ16（WZ_LHJXYH_MASTER）"
            " 同一邮件任务导入结果"
        )
        data_ok = status == "SUCCESS"
        body = format_mail_job_notify_body(
            title=title,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            base_url=base_url,
            field_rows=[
                ("目标净值日(nav_date)", report.get("nav_date")),
                ("邮件主题", report.get("target_subject")),
                ("附件文件", report.get("source_file")),
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
                "nav_date": report.get("nav_date") or "",
                "target_subject": report.get("target_subject") or "",
                "source_file": report.get("source_file") or "",
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
            "nav_date": "",
            "notify": True,
            "target_subject": "",
            "source_file": "",
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

            nav_raw = (options["nav_date"] or "").strip()
            if nav_raw:
                nav_iso = nav_raw[:10]
            else:
                nav_iso = prev_trading_day_iso_before(run_iso) or ""
                if not nav_iso:
                    report["status"] = "FAILED"
                    report["message"] = (
                        "无法在 trade_calendar 中解析「运行日之前最近一个交易日」。"
                    )
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

            report["nav_date"] = nav_iso
            nav_td = is_trade_date_iso(nav_iso)
            subject_variants = build_wz_lyh_nav_mail_subject_variants(nav_iso)
            report["target_subject"] = " | ".join(subject_variants)
            # 与 trade_calendar 的 nav_date（默认运行日上一交易日）同一日历日，用于 INTERNALDATE 筛选
            mail_anchor_day = date.fromisoformat(nav_iso)

            self.stdout.write(f"目标净值日(nav_date): {nav_iso}")

            span_err = validate_mail_job_query_span(
                query_iso=nav_iso,
                run_iso=run_iso,
                force=bool(options["force"]),
            )
            if span_err:
                report["status"] = "FAILED"
                report["message"] = span_err
                self.stderr.write(self.style.ERROR(span_err))
                return

            if nav_raw and not options["force"] and not nav_td:
                report["status"] = "SKIPPED"
                report["message"] = (
                    f"{nav_iso} 非交易日（trade_calendar），跳过。"
                    "使用 --force 可强制执行。"
                )
                self.stdout.write(self.style.WARNING(report["message"]))
                return

            email_user, email_pass, imap_server, imap_port = resolve_imap_credentials()
            self.stdout.write(f"IMAP: {email_user} @ {imap_server}:{imap_port}")

            ymd = nav_iso.replace("-", "")
            save_root = Path(settings.ALPHADATA_DIR) / "wz_lyh_nav_mail" / ymd

            mailbox: imaplib.IMAP4_SSL | None = None
            try:
                mailbox = imap_open_inbox(
                    email_user, email_pass, imap_server, imap_port
                )
                self.stdout.write(
                    f"主题（共 {len(subject_variants)} 种引号变体）: {report['target_subject']}"
                )
                self.stdout.write(
                    f"IMAP：INTERNALDATE 为 {mail_anchor_day.isoformat()}（与 nav_date 同日）"
                    f"且主题完全匹配的邮件将遍历（新→旧）；STZ049+SASQ16 均落库后即停止。"
                )

                mail_ids = list_mail_ids_on_internal_calendar_day_matching_subjects(
                    mailbox, mail_anchor_day, subject_variants
                )
                if not mail_ids:
                    for subj in subject_variants:
                        mid_fb = find_latest_mail_id_by_exact_subject(mailbox, subj)
                        if mid_fb:
                            mail_ids = [mid_fb]
                            self.stdout.write(
                                self.style.WARNING(
                                    f"nav_date 当日无 INTERNALDATE 命中，回退全箱最新主题匹配: mail id={mid_fb}"
                                )
                            )
                            break

                if not mail_ids:
                    report["status"] = "FAILED"
                    report["message"] = (
                        f"未找到 INTERNALDATE 为 {mail_anchor_day.isoformat()}（nav_date 当日）"
                        f"且主题在 {subject_variants!r} 中的邮件；全箱最新回退亦无。"
                    )
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                self.stdout.write(
                    f"共 {len(mail_ids)} 封候选邮件（直至两产品均解析成功或邮件用尽）。"
                )

                pending: dict[str, FundNavProduct] = {
                    f["product_key"]: f for f in get_wz_lyh_nav_mail_bundle_funds()
                }
                imports_meta: dict[str, str] = {}
                last_parse_err: BaseException | None = None

                for mail_id in reversed(mail_ids):
                    if not pending:
                        break
                    st2, msg_data = mailbox.fetch(mail_id, "(RFC822)")
                    if st2 != "OK" or not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1]
                    if not isinstance(raw, (bytes, bytearray)):
                        continue

                    mail_dir = save_root / f"mail_{mail_id}"
                    files = save_excel_attachments_from_rfc822(raw, mail_dir)
                    if not files:
                        self.stdout.write(
                            self.style.WARNING(f"邮件 id={mail_id} 无 Excel 附件，跳过。")
                        )
                        continue

                    files.sort(key=lambda p: (-_score_wz_lyh_nav_attachment(p), p.name))
                    for fp in files:
                        if not pending:
                            break
                        data = fp.read_bytes()
                        for pk, fund in list(pending.items()):
                            try:
                                doc = parse_fund_nav_excel(
                                    data,
                                    filename=fp.name,
                                    fund=fund,
                                    expected_nav_iso=nav_iso,
                                )
                            except ValueError as exc:
                                last_parse_err = exc
                                self.stdout.write(
                                    self.style.WARNING(
                                        f"邮件 id={mail_id} 附件 {fp.name!r} "
                                        f"未解析 {fund['asset_code']}: {exc}"
                                    )
                                )
                                continue

                            upsert_fund_nav_doc(
                                doc,
                                fund=fund,
                                source_subject=subject_variants[0],
                            )
                            imports_meta[pk] = f"{fp.name} (mail id={mail_id})"
                            del pending[pk]
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"[{pk}] 已落库 nav_date={doc['nav_date']} "
                                    f"<- {fp.name} (mail id={mail_id})"
                                )
                            )

                if pending:
                    missing = "、".join(
                        f"{p['asset_code']}（{p['product_key']}）"
                        for p in pending.values()
                    )
                    report["status"] = "FAILED"
                    report["source_file"] = (
                        "; ".join(f"{k}: {v}" for k, v in sorted(imports_meta.items()))
                        or "-"
                    )
                    report["message"] = (
                        f"未全部落库，仍缺: {missing}。"
                        f"已成功: {report['source_file']}"
                    )
                    if last_parse_err is not None:
                        report["error"] = str(last_parse_err)
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                report["status"] = "SUCCESS"
                report["source_file"] = "; ".join(
                    f"{k}: {v}" for k, v in sorted(imports_meta.items())
                )
                report["message"] = f"全部落库 nav_date={nav_iso} | {report['source_file']}"
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
