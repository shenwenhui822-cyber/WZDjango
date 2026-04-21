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

from portal.db.mongo import get_mongo_client
from portal.services.imap_common import (
    decode_mime_header,
    find_latest_mail_id_by_exact_subject,
    normalize_attachment_filename,
)
from portal.services.mail_import_common import (
    imap_logout_safe,
    imap_open_inbox,
    send_alpha_notify_result_email,
)
from portal.services.cjqh_settle_service import extract_cjqh_record_from_rar

DEFAULT_ACCOUNT_ID = "81801575"
SUBJECT_PREFIX = "81801575-吾执泽鑫多维-长江期货-"
RAR_NAME_TEMPLATE = "{account_id}-吾执泽鑫多维-{ymd}.rar"


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


def _save_target_rar_attachment(
    mailbox: imaplib.IMAP4_SSL,
    mail_id: str,
    *,
    output_dir: Path,
    account_id: str,
    ymd: str,
) -> Path:
    status, msg_data = mailbox.fetch(mail_id, "(RFC822)")
    if status != "OK" or not msg_data or not msg_data[0]:
        raise RuntimeError("读取目标邮件失败。")

    msg = email.message_from_bytes(msg_data[0][1])
    output_dir.mkdir(parents=True, exist_ok=True)
    exact_name = RAR_NAME_TEMPLATE.format(account_id=account_id, ymd=ymd)
    exact_pattern = re.compile(rf"^{re.escape(exact_name)}$", re.IGNORECASE)
    loose_pattern = re.compile(
        rf"^{re.escape(account_id)}-吾执泽鑫多维-\d{{8}}\.rar$",
        re.IGNORECASE,
    )

    candidate_name = ""
    candidate_payload: bytes | None = None
    for part in msg.walk():
        filename_raw = part.get_filename()
        if not filename_raw:
            continue
        name = normalize_attachment_filename(decode_mime_header(filename_raw))
        if not name.lower().endswith(".rar"):
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
    raise RuntimeError(f"邮件中未找到目标 RAR 附件（{exact_name}）。")


def _ymd_to_iso(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _ascii_safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip())
    return cleaned.strip("_") or "statement.txt"


class Command(BaseCommand):
    help = "按主题下载长江期货结算 RAR，解析 Account Summary 并写入 future_settle_real.CJQH_81801575"

    def add_arguments(self, parser):
        parser.add_argument(
            "--subject-date",
            default="",
            help="主题日期，格式 YYYYMMDD 或 YYYY-MM-DD；默认今天（Asia/Shanghai）。",
        )
        parser.add_argument(
            "--days",
            default=5,
            type=int,
            help="IMAP 检索范围（近 N 天，默认 5）。",
        )
        parser.add_argument(
            "--account-id",
            default=DEFAULT_ACCOUNT_ID,
            help="账号（默认 81801575）。",
        )

    def _send_result_email(self, report: dict[str, object], started_at, ended_at, base_url: str) -> None:
        duration = ended_at - started_at
        if isinstance(duration, timedelta):
            duration_sec = round(duration.total_seconds(), 3)
        else:
            duration_sec = 0.0
        status = str(report.get("status") or "UNKNOWN")
        subject = f"[{status}] 长江期货结算单入库 {timezone.localdate().strftime('%Y-%m-%d')}"
        body = "\n".join(
            [
                "长江期货结算单自动入库执行结果",
                "",
                f"状态: {status}",
                f"开始时间: {timezone.localtime(started_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"结束时间: {timezone.localtime(ended_at).strftime('%Y-%m-%d %H:%M:%S')}",
                f"运行时长(秒): {duration_sec}",
                f"服务地址: {base_url or '-'}",
                f"目标主题: {report.get('target_subject') or '-'}",
                f"主题日期: {report.get('subject_ymd') or '-'}",
                f"交易日(trade_date): {report.get('trade_date') or '-'}",
                f"账号(account_id): {report.get('account_id') or '-'}",
                f"RAR 文件: {report.get('source_rar_file') or '-'}",
                f"TXT 文件: {report.get('source_txt_file_ascii') or '-'}",
                f"结果说明: {report.get('message') or '-'}",
                f"异常信息: {report.get('error') or '-'}",
            ]
        )
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
            "subject_ymd": "",
            "target_subject": "",
            "trade_date": "",
            "account_id": "",
            "source_rar_file": "",
            "source_txt_file_ascii": "",
            "message": "",
            "error": "",
            "notify": True,
        }
        ymd = _normalize_ymd(
            (options.get("subject_date") or "").strip()
            or timezone.localdate().strftime("%Y%m%d")
        )
        report["subject_ymd"] = ymd
        account_id = str(options.get("account_id") or DEFAULT_ACCOUNT_ID).strip()
        report["account_id"] = account_id
        lookback_days = max(1, int(options.get("days") or 5))
        target_subject = f"{SUBJECT_PREFIX}{ymd}"
        report["target_subject"] = target_subject
        since_date = timezone.localdate() - timedelta(days=lookback_days)

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

                attach_root = Path(
                    getattr(
                        settings,
                        "CJQH_SETTLE_ATTACH_DIR",
                        settings.BASE_DIR / "downloaded_attachments_cjqh",
                    )
                )
                attach_dir = attach_root / ymd
                rar_path = _save_target_rar_attachment(
                    mailbox,
                    mail_id,
                    output_dir=attach_dir,
                    account_id=account_id,
                    ymd=ymd,
                )
                report["source_rar_file"] = rar_path.name
                self.stdout.write(self.style.SUCCESS(f"已下载目标 RAR: {rar_path}"))

                parsed = extract_cjqh_record_from_rar(
                    rar_path,
                    account_id=account_id,
                    ymd=ymd,
                )
                statement_ymd = str(parsed["statement_ymd"])
                trade_date = _ymd_to_iso(statement_ymd)
                report["trade_date"] = trade_date
                source_txt_file_ascii = _ascii_safe_name(str(parsed["txt_file_name"]))
                report["source_txt_file_ascii"] = source_txt_file_ascii

                payload = {
                    "trade_date": trade_date,
                    "account_id": str(parsed["client_id"] or account_id),
                    "subject_ymd": ymd,
                    "source_rar_file": rar_path.name,
                    "source_txt_file_ascii": source_txt_file_ascii,
                    "metrics": parsed["metrics"],
                    "updated_at": timezone.now().isoformat(),
                }

                client = get_mongo_client()
                try:
                    db_name = getattr(settings, "MONGODB_CJQH_SETTLE_DB", "future_settle_real")
                    coll_name = getattr(settings, "MONGODB_CJQH_SETTLE_COLLECTION", "CJQH_81801575")
                    coll = client[db_name][coll_name]
                    coll.create_index(
                        [("trade_date", 1), ("account_id", 1)],
                        unique=True,
                        background=True,
                    )
                    result = coll.update_one(
                        {"trade_date": trade_date, "account_id": payload["account_id"]},
                        {"$set": payload},
                        upsert=True,
                    )
                finally:
                    client.close()

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
