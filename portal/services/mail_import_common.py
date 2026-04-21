"""
邮件自动导入共用逻辑：交易日跨度校验、IMAP 连接/关闭、Excel 附件落盘、结果通知（ALPHA_NOTIFY_*）。
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from smtplib import SMTPException, SMTP_SSL
from typing import Callable

from portal.services.imap_common import decode_mime_header, normalize_attachment_filename
from portal.services.trade_calendar_service import count_trading_days_inclusive


def max_mail_job_trading_day_span() -> int:
    raw = (os.getenv("MAIL_JOB_MAX_TRADING_DAY_SPAN") or "3").strip()
    try:
        n = int(raw)
        return max(1, min(n, 30))
    except ValueError:
        return 3


def iso_date_from_yyyymmdd(ymd: str) -> str:
    s = (ymd or "").strip()
    if len(s) >= 8 and re.fullmatch(r"\d{8}", s[:8]):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def validate_mail_job_query_span(
    *,
    query_iso: str,
    run_iso: str,
    force: bool,
    max_inclusive_trading_days: int | None = None,
) -> str | None:
    """
    非 force 时：查询日 query 与运行日 run 闭区间内的交易日个数不得超过上限（默认 3）。
    """
    if force:
        return None
    m = max_inclusive_trading_days if max_inclusive_trading_days is not None else max_mail_job_trading_day_span()
    q = (query_iso or "").strip()[:10]
    r = (run_iso or "").strip()[:10]
    if len(q) != 10 or len(r) != 10:
        return "查询日期或运行日格式无效（需 YYYY-MM-DD）。"
    if q > r:
        return f"查询日 {q} 晚于运行日 {r}。"
    n = count_trading_days_inclusive(q, r)
    if n > m:
        return (
            f"查询日 {q} 与运行日 {r} 之间（含首尾）共 {n} 个交易日，超过允许的 {m} 个交易日；"
            f"请缩小日期范围或使用 --force。"
        )
    return None


def imap_open_inbox(user: str, pwd: str, host: str, port: int) -> imaplib.IMAP4_SSL:
    mailbox = imaplib.IMAP4_SSL(host, port)
    mailbox.login(user, pwd)
    st, _ = mailbox.select("INBOX", readonly=True)
    if st != "OK":
        try:
            mailbox.logout()
        except Exception:
            pass
        raise RuntimeError("无法打开 INBOX")
    return mailbox


def imap_logout_safe(mailbox: imaplib.IMAP4_SSL | None) -> None:
    if mailbox is None:
        return
    try:
        mailbox.logout()
    except Exception:
        pass


def excel_ext_ok(filename: str) -> bool:
    _, ext = os.path.splitext((filename or "").lower())
    return ext in (".xlsx", ".xls", ".xlsm")


def save_excel_attachments_from_rfc822(msg_bytes: bytes, save_dir: Path) -> list[Path]:
    """从整封邮件 RFC822 字节流中保存 Excel 附件（与博士一号 / ZXDW 邮件任务相同逻辑）。"""
    msg = email.message_from_bytes(msg_bytes)
    save_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for part in msg.walk():
        disp = str(part.get("Content-Disposition", ""))
        if "attachment" not in disp.lower():
            continue
        filename_raw = part.get_filename()
        filename = normalize_attachment_filename(
            decode_mime_header(filename_raw) if filename_raw else ""
        )
        if not excel_ext_ok(filename):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        output = save_dir / filename
        if output.exists():
            stem, ext = output.stem, output.suffix
            i = 1
            while True:
                candidate = save_dir / f"{stem}_{i}{ext}"
                if not candidate.exists():
                    output = candidate
                    break
                i += 1
        output.write_bytes(payload)
        saved.append(output)
    return saved


def send_alpha_notify_result_email(
    *,
    mail_subject: str,
    body: str,
    log_stdout: Callable[[str], None],
    log_stderr_warn: Callable[[str], None],
) -> None:
    """使用环境变量 ALPHA_NOTIFY_* 发送纯文本结果邮件。"""
    to_raw = os.getenv("ALPHA_NOTIFY_TO", "")
    recipients = [x.strip() for x in to_raw.split(",") if x.strip()]
    smtp_host = os.getenv("ALPHA_NOTIFY_SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("ALPHA_NOTIFY_SMTP_PORT", "465"))
    smtp_user = os.getenv("ALPHA_NOTIFY_USER", "").strip()
    smtp_pass = os.getenv("ALPHA_NOTIFY_PASS", "").strip()
    sender = os.getenv("ALPHA_NOTIFY_FROM", smtp_user).strip()

    if not recipients:
        log_stdout("未配置 ALPHA_NOTIFY_TO，跳过结果通知邮件。")
        return
    if not (smtp_host and smtp_user and smtp_pass and sender):
        log_stdout("通知邮箱 SMTP 配置不完整，跳过结果通知邮件。")
        return

    msg = MIMEMultipart()
    msg["Subject"] = mail_subject
    msg["From"] = sender
    msg["To"] = ";".join(recipients)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        smtp = SMTP_SSL(smtp_host, smtp_port)
        smtp.login(smtp_user, smtp_pass)
        smtp.sendmail(sender, recipients, msg.as_string())
        smtp.quit()
        log_stdout(f"结果通知邮件已发送: {', '.join(recipients)}")
    except SMTPException as exc:
        log_stderr_warn(f"结果通知邮件发送失败: {exc}")
