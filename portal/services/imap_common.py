"""IMAP 通用：解码主题/附件名、同主题多封取最新 Date。"""
from __future__ import annotations

import email
import imaplib
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
    """遍历 INBOX，按「基金关键词 + 报告日期 YYYYMMDD」匹配主题；多封取 Date 最新。"""
    status, data = mailbox.search(None, "ALL")
    if status != "OK" or not data or not data[0]:
        return None
    matched: list[bytes] = []
    for raw_id in data[0].split():
        mail_id = raw_id.decode()
        status, msg_data = mailbox.fetch(mail_id, "(BODY[HEADER.FIELDS (SUBJECT)])")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_mime_header(msg.get("Subject", "")).strip()
        if subject_matches_fund_and_report_date(subject, fund_phrases, report_date_ymd):
            matched.append(raw_id)
    if not matched:
        return None
    return select_latest_mail_id_by_date_header(mailbox, matched)


def find_latest_mail_id_by_exact_subject(
    mailbox: imaplib.IMAP4_SSL, target_subject: str
) -> str | None:
    status, data = mailbox.search(None, "ALL")
    if status != "OK":
        return None
    matched: list[bytes] = []
    for raw_id in data[0].split():
        mail_id = raw_id.decode()
        status, msg_data = mailbox.fetch(mail_id, "(BODY[HEADER.FIELDS (SUBJECT)])")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_mime_header(msg.get("Subject", "")).strip()
        if subject == target_subject:
            matched.append(raw_id)
    if not matched:
        return None
    return select_latest_mail_id_by_date_header(mailbox, matched)
