"""IMAP 通用：解码主题/附件名；主题匹配均从收件箱较新邮件往旧遍历，命中即停止。"""
from __future__ import annotations

import email
import imaplib
from datetime import date
from email.header import decode_header
from email.utils import parsedate_to_datetime


def decode_mime_header(value: str) -> str:
    if not value:
        return ""
    out = ""
    for text, enc in decode_header(value):
        if isinstance(text, bytes):
            try:
                out += text.decode(enc or "utf-8")
            except Exception:
                out += text.decode("gbk", errors="ignore")
        else:
            out += text
    return out


def normalize_attachment_filename(name: str) -> str:
    clean = (name or "").strip().replace("\r", "").replace("\n", "")
    return clean or "attachment.bin"


def select_latest_mail_id_by_date_header(
    mailbox: imaplib.IMAP4_SSL, ids: list[bytes]
) -> str | None:
    """多封同主题时取邮件 Date 最新的一封。"""
    latest_id: str | None = None
    latest_dt = None
    for raw_id in ids:
        mail_id = raw_id.decode()
        status, msg_data = mailbox.fetch(mail_id, "(BODY[HEADER.FIELDS (DATE)])")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        raw_date = msg.get("Date", "")
        try:
            dt = parsedate_to_datetime(raw_date)
        except Exception:
            dt = None

        if latest_id is None:
            latest_id = mail_id
            latest_dt = dt
            continue

        if dt is not None and (latest_dt is None or dt > latest_dt):
            latest_id = mail_id
            latest_dt = dt
    return latest_id


def subject_matches_fund_and_report_date(
    subject: str,
    fund_phrases: tuple[str, ...],
    report_date_ymd: str,
) -> bool:
    """主题须包含报告日期 YYYYMMDD，且至少包含一条基金关键词（子串）。"""
    subj = (subject or "").strip()
    ymd = (report_date_ymd or "").strip()
    if not ymd or ymd not in subj:
        return False
    return any((p or "").strip() and (p in subj) for p in fund_phrases)


def find_mail_id_by_fuzzy_fund_subject(
    mailbox: imaplib.IMAP4_SSL,
    fund_phrases: tuple[str, ...],
    report_date_ymd: str,
) -> str | None:
    """从较新到较旧遍历 INBOX，按「基金关键词 + 报告日期 YYYYMMDD」匹配主题，命中第一封即返回。"""
    status, data = mailbox.search(None, "ALL")
    if status != "OK" or not data or not data[0]:
        return None
    for raw_id in reversed(data[0].split()):
        mail_id = raw_id.decode()
        status, msg_data = mailbox.fetch(mail_id, "(BODY[HEADER.FIELDS (SUBJECT)])")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_mime_header(msg.get("Subject", "")).strip()
        if subject_matches_fund_and_report_date(subject, fund_phrases, report_date_ymd):
            return mail_id
    return None


def find_latest_mail_id_by_exact_subject(
    mailbox: imaplib.IMAP4_SSL,
    target_subject: str,
    *,
    since_calendar_date: date | None = None,
) -> str | None:
    """从较新到较旧遍历，主题与 target_subject 完全一致则立即返回该封。

    since_calendar_date：若给定，则只在该日历日及之后的邮件中搜索（IMAP SINCE），避免全箱扫描。
    """
    if since_calendar_date is not None:
        crit = f'SINCE "{since_calendar_date.strftime("%d-%b-%Y")}"'
    else:
        crit = "ALL"
    status, data = mailbox.search(None, crit)
    if status != "OK" or not data or not data[0]:
        return None
    want = (target_subject or "").strip()
    for raw_id in reversed(data[0].split()):
        mail_id = raw_id.decode()
        status, msg_data = mailbox.fetch(mail_id, "(BODY[HEADER.FIELDS (SUBJECT)])")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_mime_header(msg.get("Subject", "")).strip()
        if subject == want:
            return mail_id
    return None
