from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.db.mongo import close_mongo_client, get_mongo_client
from portal.services.imap_common import (
    decode_mime_header,
    find_latest_mail_id_by_exact_subject,
    normalize_attachment_filename,
)
from portal.services.mail_import_common import (
    emit_mail_job_result_line,
    format_mail_job_notify_body,
    imap_logout_safe,
    imap_open_inbox,
    mail_job_notify_base,
    send_alpha_notify_result_email,
)
from portal.services.ghzq_settle_service import extract_ghzq_statement_from_xlsx
from portal.services.trade_calendar_service import prev_trading_day_iso_before

# 邮件主题：账户对账单_{subject_fund_code}_吾执九五号_{YYYYMMDD}_融资融券账户对账单
DEFAULT_SUBJECT_FUND_CODE = "37208761"


def _resolve_farport_imap_credentials() -> tuple[str, str, str, int]:
    user = (os.getenv("FARPORT_MAIL_USER") or "").strip()
    pwd = (os.getenv("FARPORT_MAIL_PASS") or "").strip()
    host = (os.getenv("ALPHA_IMAP_SERVER") or "imap.exmail.qq.com").strip()
    port = int(os.getenv("ALPHA_IMAP_PORT") or "993")
    if not (user and pwd):
        raise RuntimeError("未配置 FARPORT 邮箱，请在 .env 设置 FARPORT_MAIL_USER/FARPORT_MAIL_PASS。")
    return user, pwd, host, port


def _normalize_ymd(raw: str) -> str:
    s = (raw or "").strip().replace("-", "").replace("/", "")
    if not re.fullmatch(r"\d{8}", s):
        raise ValueError(f"日期格式错误: {raw!r}，应为 YYYYMMDD 或 YYYY-MM-DD")
    return s


def _ymd_to_iso(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _safe_output_path(output_dir: Path, filename: str) -> Path:
    target = output_dir / filename
    if not target.exists():
        return target
    stem, ext = target.stem, target.suffix
    idx = 1
    while True:
        p = output_dir / f"{stem}_{idx}{ext}"
        if not p.exists():
            return p
        idx += 1


def _ascii_safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip())
    return cleaned.strip("_") or "statement.xlsx"


def _save_target_xlsx_attachment(
    mailbox: imaplib.IMAP4_SSL,
    mail_id: str,
    *,
    output_dir: Path,
    subject_fund_code: str,
    ymd: str,
) -> Path:
    status, msg_data = mailbox.fetch(mail_id, "(RFC822)")
    if status != "OK" or not msg_data or not msg_data[0]:
        raise RuntimeError("读取目标邮件失败。")

    msg = email.message_from_bytes(msg_data[0][1])
    output_dir.mkdir(parents=True, exist_ok=True)
    exact_name = f"{subject_fund_code}_吾执九五号_{ymd}_融资融券账户对账单.xlsx"
    exact_pattern = re.compile(rf"^{re.escape(exact_name)}$", re.IGNORECASE)
    loose_pattern = re.compile(
        rf".*{re.escape(subject_fund_code)}.*吾执九五号.*{re.escape(ymd)}.*\.xlsx$",
        re.IGNORECASE,
    )

    candidate_name = ""
    candidate_payload: bytes | None = None
    for part in msg.walk():
        filename_raw = part.get_filename()
        if not filename_raw:
            continue
        name = normalize_attachment_filename(decode_mime_header(filename_raw))
        if not name.lower().endswith(".xlsx"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if exact_pattern.match(name):
            save_path = _safe_output_path(output_dir, name)
            save_path.write_bytes(payload)
            return save_path
        if not candidate_payload and loose_pattern.match(name):
            candidate_name = name
            candidate_payload = payload

    if candidate_payload:
        save_path = _safe_output_path(output_dir, candidate_name)
        save_path.write_bytes(candidate_payload)
        return save_path

    for part in msg.walk():
        filename_raw = part.get_filename()
        if not filename_raw:
            continue
        name = normalize_attachment_filename(decode_mime_header(filename_raw))
        if not name.lower().endswith(".xlsx"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if ymd in name and "吾执九五号" in name:
            save_path = _safe_output_path(output_dir, name)
            save_path.write_bytes(payload)
            return save_path

    raise RuntimeError(f"邮件中未找到目标 xlsx 附件（预期含 {subject_fund_code}、{ymd}）。")


class Command(BaseCommand):
    help = "国海证券吾执九五号融资融券对账单邮件：下载 xlsx 并写入 settings 配置的 future_settle_real.GHZQ_17190083"

    def add_arguments(self, parser):
        parser.add_argument(
            "--subject-date",
            default="",
            help="主题中的日期 YYYYMMDD 或 YYYY-MM-DD；省略则按交易日历取运行日的前一交易日（需已导入 trade_calendar）。",
        )
        parser.add_argument(
            "--days",
            default=7,
            type=int,
            help="IMAP 检索范围（近 N 天，默认 7）。",
        )
        parser.add_argument(
            "--subject-fund-code",
            default=DEFAULT_SUBJECT_FUND_CODE,
            help=f"主题内 fund 段（默认 {DEFAULT_SUBJECT_FUND_CODE}）。",
        )

    def _send_result_email(self, report: dict[str, object], started_at, ended_at, base_url: str) -> None:
        status = str(report.get("status") or "UNKNOWN")
        subject = f"[{status}] 国海证券对账单入库 {timezone.localdate().strftime('%Y-%m-%d')}"
        title = "国海证券吾执九五号对账单自动入库执行结果"
        data_ok = status == "SUCCESS"
        body = format_mail_job_notify_body(
            title=title,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            base_url=base_url,
            field_rows=[
                ("运行日", report.get("run_date")),
                ("主题对账单日期", report.get("statement_ymd")),
                ("目标主题", report.get("target_subject")),
                ("交易日", report.get("trade_date")),
                ("资金账号", report.get("fund_account_id")),
                ("xlsx 文件", report.get("source_xlsx_file")),
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
                "run_date": report.get("run_date") or "",
                "statement_ymd": report.get("statement_ymd") or "",
                "target_subject": report.get("target_subject") or "",
                "trade_date": report.get("trade_date") or "",
                "fund_account_id": report.get("fund_account_id") or "",
                "source_xlsx_file": report.get("source_xlsx_file") or "",
                "message": report.get("message") or "",
                "error": report.get("error") or "",
            }
        )
        emit_mail_job_result_line(self.stdout.write, snap)
        send_alpha_notify_result_email(
            mail_subject=subject,
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
            "statement_ymd": "",
            "target_subject": "",
            "trade_date": "",
            "fund_account_id": "",
            "source_xlsx_file": "",
            "message": "",
            "error": "",
            "notify": True,
        }
        raw_subject = (options.get("subject_date") or "").strip()
        if raw_subject:
            ymd = _normalize_ymd(raw_subject)
        else:
            today_iso = timezone.localdate().isoformat()
            prev_iso = prev_trading_day_iso_before(today_iso)
            if not prev_iso:
                raise RuntimeError(
                    "未指定 --subject-date 时需按前一交易日查询对账单，但无法从 trade_calendar "
                    "解析前一交易日（请先 python manage.py import_trade_dates_csv）。"
                )
            ymd = prev_iso.replace("-", "")
            self.stdout.write(
                self.style.WARNING(
                    f"未指定 --subject-date：使用运行日前一交易日主题日 {ymd}（运行日 {today_iso}）。"
                )
            )
        report["statement_ymd"] = ymd
        subject_fund_code = str(options.get("subject_fund_code") or DEFAULT_SUBJECT_FUND_CODE).strip()
        lookback_days = max(1, int(options.get("days") or 7))
        target_subject = (
            f"账户对账单_{subject_fund_code}_吾执九五号_{ymd}_融资融券账户对账单"
        )
        report["target_subject"] = target_subject
        since_date = timezone.localdate() - timedelta(days=lookback_days)

        db_name = getattr(settings, "GHZQ_17190083_SETTLE_DB", "future_settle_real")
        coll_name = getattr(settings, "GHZQ_17190083_SETTLE_COLLECTION", "GHZQ_17190083")
        attach_root = Path(
            getattr(
                settings,
                "GHZQ_17190083_SETTLE_ATTACH_DIR",
                settings.BASE_DIR / "downloaded_attachments_ghzq",
            )
        )

        self.stdout.write(f"目标主题: {target_subject}")
        user, pwd, host, port = _resolve_farport_imap_credentials()
        mailbox: imaplib.IMAP4_SSL | None = None
        try:
            try:
                mailbox = imap_open_inbox(user, pwd, host, port)
                mail_id = find_latest_mail_id_by_exact_subject(
                    mailbox,
                    target_subject,
                    since_calendar_date=since_date,
                )
                if not mail_id:
                    raise RuntimeError("未找到匹配主题的邮件。")

                attach_dir = attach_root / ymd
                xlsx_path = _save_target_xlsx_attachment(
                    mailbox,
                    mail_id,
                    output_dir=attach_dir,
                    subject_fund_code=subject_fund_code,
                    ymd=ymd,
                )
                report["source_xlsx_file"] = xlsx_path.name
                self.stdout.write(self.style.SUCCESS(f"已下载 xlsx: {xlsx_path}"))

                parsed = extract_ghzq_statement_from_xlsx(xlsx_path)
                trade_date = _ymd_to_iso(ymd)
                report["trade_date"] = trade_date
                fund_account_id = str(parsed.get("fund_account_id") or "").strip()
                report["fund_account_id"] = fund_account_id

                payload = {
                    "trade_date": trade_date,
                    "subject_ymd": ymd,
                    "subject_fund_code": subject_fund_code,
                    "fund_account_id": fund_account_id,
                    "metrics": parsed.get("metrics") or {},
                    "source_xlsx_file": xlsx_path.name,
                    "source_xlsx_file_ascii": _ascii_safe_name(xlsx_path.name),
                    "updated_at": timezone.now().isoformat(),
                }

                client = get_mongo_client()
                try:
                    coll = client[db_name][coll_name]
                    coll.create_index(
                        [("trade_date", 1), ("fund_account_id", 1)],
                        unique=True,
                        background=True,
                    )
                    result = coll.update_one(
                        {
                            "trade_date": trade_date,
                            "fund_account_id": fund_account_id or "17190083",
                        },
                        {"$set": payload},
                        upsert=True,
                    )
                finally:
                    close_mongo_client()

                report["status"] = "SUCCESS"
                report["message"] = (
                    f"入库完成: {db_name}.{coll_name} "
                    f"(matched={result.matched_count}, modified={result.modified_count}, "
                    f"upserted={result.upserted_id is not None})"
                )
                self.stdout.write(self.style.SUCCESS(str(report["message"])))
            except Exception as exc:
                report["status"] = "FAILED"
                report["error"] = str(exc)
                report["message"] = str(exc)
                raise
        finally:
            imap_logout_safe(mailbox)
            if report.get("notify", True):
                self._send_result_email(report, started_at, timezone.now(), base_url)
