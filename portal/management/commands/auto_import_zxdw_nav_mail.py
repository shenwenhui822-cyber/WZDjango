"""
交易日中午前后：按「前一交易日」报告日期匹配邮件主题，下载净值 Excel，
按产品代码写入 fund_nav_real 下 WZ_ZXDW_MASTER / WZ_ZXDW_A|B|C（见 settings.MONGODB_ZXDW_NAV_COLLECTIONS）。

调度：portal.scheduler.alpha_mail_scheduler 默认 12:00（环境变量 ZXDW_NAV_MAIL_SCHEDULER_ENABLED）。

业务约定：未指定 --report-date 时，默认按运行日的前一交易日（T-1）查询并导入；
指定单日则只查该日。
邮箱登录账号从 .env 读取 FARPORT_MAIL_USER / FARPORT_MAIL_PASS。
任务结束后按 ALPHA_NOTIFY_* 发送结果邮件（非交易日跳过时不发）。

用法：
  python manage.py auto_import_zxdw_nav_mail
  python manage.py auto_import_zxdw_nav_mail --force
  python manage.py auto_import_zxdw_nav_mail --report-date 2026-04-15
"""
from __future__ import annotations

import imaplib
import os
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.services.imap_common import find_latest_mail_id_by_exact_subject
from portal.services.mail_import_common import (
    imap_logout_safe,
    imap_open_inbox,
    max_mail_job_trading_day_span,
    save_excel_attachments_from_rfc822,
    send_alpha_notify_result_email,
    validate_mail_job_query_span,
)
from portal.services.trade_calendar_service import (
    is_trade_date_iso,
    last_n_prev_trading_day_isos,
    prev_trading_day_iso_before,
)
from portal.services.zxdw_fund_nav_service import import_zxdw_excel_routed_by_asset_code

# 主题匹配规则：固定前缀 + 报告日 YYYYMMDD
ZXDW_NAV_MAIL_FUND_KEY_PHRASES: tuple[str, ...] = (
    "【净值表】上海吾执投资管理有限公司吾执泽鑫多维产品净值表发送-管理人",
)

def _resolve_imap_credentials_from_env_v1() -> tuple[str, str, str, int]:
    """
    从 .env / 环境变量读取邮箱配置（_v1 版本键名）。
    """
    user = (os.getenv("FARPORT_MAIL_USER") or "").strip()
    pwd = (os.getenv("FARPORT_MAIL_PASS") or "").strip()
    host = (os.getenv("ALPHA_IMAP_SERVER") or "imap.exmail.qq.com").strip()
    port = int(os.getenv("ALPHA_IMAP_PORT") or "993")
    if not (user and pwd):
        raise RuntimeError(
            "未配置邮箱：请在 .env 中设置 FARPORT_MAIL_USER、FARPORT_MAIL_PASS。"
        )
    return user, pwd, host, port


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
            help=(
                "报告日期 YYYY-MM-DD；未指定时按交易日历取运行日的前一交易日（T-1）。"
            ),
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
                f"服务地址: {base_url or '-'}",
                f"报告日(report_date): {report.get('report_date') or '-'}",
                f"主题日期(ymd): {report.get('ymd') or '-'}",
                f"尝试过的报告日: {report.get('report_dates_tried') or '-'}",
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
            "notify": True,
            "report_date": "",
            "ymd": "",
            "message": "",
            "error": "",
            "total_upsert": 0,
            "success_lines": [],
            "warn_lines": [],
            "report_dates_tried": "",
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
            explicit_date = bool(raw_report)
            if explicit_date:
                report_isos_to_try = [raw_report[:10]]
            else:
                prev_iso = prev_trading_day_iso_before(today_iso)
                if not prev_iso:
                    report["status"] = "FAILED"
                    report["message"] = (
                        "未指定 --report-date 时需按前一交易日查询，"
                        "但无法从 trade_calendar 解析前一交易日。"
                    )
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return
                report_isos_to_try = [prev_iso]

            report["report_dates_tried"] = ", ".join(report_isos_to_try)

            oldest = report_isos_to_try[-1]
            max_span = (
                max_mail_job_trading_day_span()
                if explicit_date
                else max(5, max_mail_job_trading_day_span())
            )
            span_err = validate_mail_job_query_span(
                query_iso=oldest,
                run_iso=today_iso,
                force=force,
                max_inclusive_trading_days=max_span,
            )
            if span_err:
                report["status"] = "FAILED"
                report["message"] = span_err
                self.stderr.write(self.style.ERROR(span_err))
                return

            subject_prefixes = ZXDW_NAV_MAIL_FUND_KEY_PHRASES
            if not subject_prefixes:
                report["status"] = "FAILED"
                report["message"] = "ZXDW_NAV_MAIL_FUND_KEY_PHRASES 为空，请在本文件顶部配置"
                self.stderr.write(self.style.ERROR(report["message"]))
                return

            user, pwd, host, port = _resolve_imap_credentials_from_env_v1()
            try:
                mailbox = imap_open_inbox(user, pwd, host, port)

                self.stdout.write(
                    "按完整主题精确匹配（非模糊）；"
                    + (
                        "未指定 --report-date：仅使用运行日的前一交易日（T-1）尝试主题。"
                        if not explicit_date
                        else "已指定 --report-date，仅尝试该日。"
                    )
                )
                self.stdout.write(
                    "检索 IMAP 时仅搜索报告日前后一段日期范围内的邮件，避免全箱扫描（大邮箱仍需数秒至数十秒）。"
                )

                mail_id: str | None = None
                matched_subject = ""
                report_iso = ""
                ymd = ""

                for cand_iso in report_isos_to_try:
                    cand_ymd = cand_iso.replace("-", "")
                    target_subjects = [f"{prefix}{cand_ymd}" for prefix in subject_prefixes]
                    self.stdout.write(
                        f"---- 尝试报告日 {cand_iso}（主题后缀 {cand_ymd}）----"
                    )
                    for sub in target_subjects:
                        self.stdout.write(f"  目标主题：{sub}")
                    span_td = max_mail_job_trading_day_span()
                    prev_trade_isos = last_n_prev_trading_day_isos(cand_iso, span_td)
                    if prev_trade_isos:
                        since_dt = date.fromisoformat(prev_trade_isos[-1])
                    else:
                        since_dt = date.fromisoformat(cand_iso) - timedelta(days=max(7, span_td * 2))
                    self.stdout.write(
                        f"  正在检索（IMAP SINCE ≥ {since_dt.isoformat()}："
                        f"自报告日起向前回溯 {span_td} 个交易日取最远日为检索起点，"
                        f"对应 MAIL_JOB_MAX_TRADING_DAY_SPAN；非自然日递减）..."
                    )
                    for sub in target_subjects:
                        mail_id = find_latest_mail_id_by_exact_subject(
                            mailbox,
                            sub,
                            since_calendar_date=since_dt,
                        )
                        if mail_id:
                            matched_subject = sub
                            report_iso = cand_iso
                            ymd = cand_ymd
                            break
                    if mail_id:
                        break

                if not mail_id:
                    report["status"] = "FAILED"
                    report["message"] = (
                        "未找到完整主题精确匹配的邮件。"
                    )
                    report["report_date"] = report_isos_to_try[0]
                    report["ymd"] = report_isos_to_try[0].replace("-", "")
                    self.stderr.write(self.style.ERROR(report["message"]))
                    return

                report["report_date"] = report_iso
                report["ymd"] = ymd
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
                files = save_excel_attachments_from_rfc822(msg_bytes, day_dir)
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
                    stat = import_zxdw_excel_routed_by_asset_code(
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
