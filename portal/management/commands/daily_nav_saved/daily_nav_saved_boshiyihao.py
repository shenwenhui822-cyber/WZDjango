import email
import imaplib
import os
import sys
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from login_wangkan_mail import EMAIL_ADDRESS, IMAP_PORT, IMAP_SERVER, PASSWORD

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from portal.data.fund_nav_real_config import FUND_NAV_PRODUCTS, build_fund_nav_mail_subject

DOWNLOAD_DIR = Path("downloaded_attachments_boshiyihao")
PRODUCT_KEY = "WZ_BSYH_MASTER"


def get_target_subject() -> str:
    """与自动落库一致：主题为估值日 T，运行日通常为 T+1，故默认取本地（上海）昨日日期。"""
    nav_iso = (
        datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    ).isoformat()
    fund = next(f for f in FUND_NAV_PRODUCTS if f["product_key"] == PRODUCT_KEY)
    return build_fund_nav_mail_subject(fund, nav_iso)


def decode_mime_header(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    output = ""
    for text, encoding in parts:
        if isinstance(text, bytes):
            try:
                output += text.decode(encoding or "utf-8")
            except Exception:
                output += text.decode("gbk", errors="ignore")
        else:
            output += text
    return output


def normalize_filename(name: str) -> str:
    cleaned = name.strip().replace("\r", "").replace("\n", "")
    if not cleaned:
        return "attachment.bin"
    return cleaned


def select_latest_match(mailbox: imaplib.IMAP4_SSL, ids: list[bytes]) -> Optional[str]:
    latest_id = None
    latest_dt = None
    for raw_id in ids:
        mail_id = raw_id.decode()
        status, msg_data = mailbox.fetch(mail_id, "(BODY[HEADER.FIELDS (DATE)])")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        header_bytes = msg_data[0][1]
        msg = email.message_from_bytes(header_bytes)
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


def find_target_mail_id(mailbox: imaplib.IMAP4_SSL, target_subject: str) -> Optional[str]:
    status, data = mailbox.search(None, "ALL")
    if status != "OK":
        return None

    matched_ids: list[bytes] = []
    for raw_id in data[0].split():
        mail_id = raw_id.decode()
        status, msg_data = mailbox.fetch(mail_id, "(BODY[HEADER.FIELDS (SUBJECT)])")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        header_bytes = msg_data[0][1]
        msg = email.message_from_bytes(header_bytes)
        subject = decode_mime_header(msg.get("Subject", "")).strip()
        if subject == target_subject:
            matched_ids.append(raw_id)

    if not matched_ids:
        return None
    return select_latest_match(mailbox, matched_ids)


def save_attachments(mailbox: imaplib.IMAP4_SSL, mail_id: str, save_dir: Path) -> int:
    status, msg_data = mailbox.fetch(mail_id, "(RFC822)")
    if status != "OK" or not msg_data or not msg_data[0]:
        return 0

    message = email.message_from_bytes(msg_data[0][1])
    save_dir.mkdir(parents=True, exist_ok=True)

    saved_count = 0
    for part in message.walk():
        content_disposition = str(part.get("Content-Disposition", ""))
        if "attachment" not in content_disposition.lower():
            continue

        filename_raw = part.get_filename()
        filename = decode_mime_header(filename_raw) if filename_raw else "attachment.bin"
        filename = normalize_filename(filename)
        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        output_path = save_dir / filename
        if output_path.exists():
            base, ext = os.path.splitext(filename)
            idx = 1
            while True:
                candidate = save_dir / f"{base}_{idx}{ext}"
                if not candidate.exists():
                    output_path = candidate
                    break
                idx += 1

        output_path.write_bytes(payload)
        saved_count += 1
        print(f"已保存附件: {output_path}")

    return saved_count


def main() -> None:
    mailbox = None
    try:
        mailbox = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mailbox.login(EMAIL_ADDRESS, PASSWORD)
        status, _ = mailbox.select("INBOX", readonly=True)
        if status != "OK":
            print("登录成功但无法打开 INBOX")
            return

        target_subject = get_target_subject()
        print("登录成功，开始查找目标邮件（主题日期为上海时区昨日）…")
        print(f"目标主题: {target_subject}")
        target_id = find_target_mail_id(mailbox, target_subject)
        if not target_id:
            print(f"未找到主题为 `{target_subject}` 的邮件")
            return

        print(f"找到目标邮件 ID: {target_id}")
        count = save_attachments(mailbox, target_id, DOWNLOAD_DIR)
        if count == 0:
            print("目标邮件存在，但没有检测到附件")
        else:
            print(f"附件下载完成，共保存 {count} 个文件")
    except Exception as exc:
        print(f"执行失败: {exc}")
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass


if __name__ == "__main__":
    main()
