"""IMAP 按「吾执_周度绩效_{上周起}_{上周止}」拉取 CSV 至 qichat 并导入 t0_order（深度秩序周度）。"""
from __future__ import annotations

import imaplib
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.config.mail_imap import resolve_imap_credentials
from portal.services.imap_common import find_latest_mail_id_by_exact_subject
from portal.services.mail_import_common import (
    imap_logout_safe,
    imap_open_inbox,
    save_csv_attachments_from_rfc822,
)
from portal.services.trade_calendar_service import qichat_prev_iso_week_trading_ymd_pair
from portal.t0_performance.qichat_import import import_qichat_csv_files


def _build_subject(start_ymd: str, end_ymd: str) -> str:
    prefix = getattr(settings, "T0_QICHAT_MAIL_SUBJECT_PREFIX", None) or "吾执_周度绩效_"
    return f"{prefix}{start_ymd}_{end_ymd}"


class Command(BaseCommand):
    help = (
        "从收件箱按动态主题下载上周周度绩效 CSV 至 T0_QICHAT_IMPORT_DIR，并仅对新下载的文件执行入库。"
        "主题格式见 settings.T0_QICHAT_MAIL_SUBJECT_PREFIX；邮箱见 ALPHA_MAIL_USER / ALPHA_MAIL_PASS。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--ref-date",
            type=str,
            default="",
            help="基准自然日 YYYY-MM-DD（默认今天本地），用于计算「上一自然周」邮件日期范围。",
        )
        parser.add_argument(
            "--subject",
            type=str,
            default="",
            help="覆盖完整邮件主题（极少用手动补跑时使用）。",
        )

    def handle(self, *args, **options):
        ref_raw = (options.get("ref_date") or "").strip()
        if ref_raw:
            ref_iso = ref_raw[:10]
        else:
            ref_iso = timezone.localdate().isoformat()

        override_subject = (options.get("subject") or "").strip()

        if override_subject:
            target_subject = override_subject
            pair_note = "(手动指定主题)"
        else:
            bounds = qichat_prev_iso_week_trading_ymd_pair(ref_iso)
            if not bounds:
                self.stderr.write(
                    self.style.ERROR(
                        f"{ref_iso} 推算的上一自然周内无交易日历记录，无法生成邮件主题。"
                    )
                )
                return
            a, b = bounds
            target_subject = _build_subject(a, b)
            pair_note = f"{a}–{b}"

        email_user, email_pass, imap_server, imap_port = resolve_imap_credentials()
        self.stdout.write(
            f"IMAP: {email_user} @ {imap_server}:{imap_port}  主题: {target_subject} {pair_note}"
        )

        ref_d = date.fromisoformat(ref_iso)
        since_cal = ref_d - timedelta(days=21)

        qichat_dir: Path = Path(settings.T0_QICHAT_IMPORT_DIR)
        qichat_dir.mkdir(parents=True, exist_ok=True)

        mailbox: imaplib.IMAP4_SSL | None = None
        try:
            mailbox = imap_open_inbox(
                email_user, email_pass, imap_server, imap_port
            )
            mail_id = find_latest_mail_id_by_exact_subject(
                mailbox,
                target_subject,
                since_calendar_date=since_cal,
            )
            if not mail_id:
                self.stderr.write(self.style.ERROR("未找到主题完全匹配的邮件。"))
                return

            st2, msg_data = mailbox.fetch(mail_id, "(RFC822)")
            if st2 != "OK" or not msg_data or not msg_data[0]:
                self.stderr.write(self.style.ERROR("无法读取邮件正文。"))
                return

            raw = msg_data[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                self.stderr.write(self.style.ERROR("邮件内容格式异常。"))
                return

            files = save_csv_attachments_from_rfc822(raw, qichat_dir)
            if not files:
                self.stderr.write(
                    self.style.ERROR("邮件中无 CSV 附件（Content-Disposition: attachment）。")
                )
                return

            for fp in files:
                self.stdout.write(self.style.SUCCESS(f"已保存: {fp}"))

            r = import_qichat_csv_files(files)
            for e in r.errors:
                self.stdout.write(self.style.WARNING(e))
            self.stdout.write(
                self.style.SUCCESS(
                    f"入库完成：{r.files_processed} 个文件，upsert {r.rows_upserted} 行。"
                )
            )
        finally:
            imap_logout_safe(mailbox)
