"""
邮件自动导入共用逻辑：交易日跨度校验、IMAP 连接/关闭、Excel 附件落盘、结果通知（ALPHA_NOTIFY_*）。
"""
from __future__ import annotations

import email
import imaplib
import json
import os
import re
import zipfile
from datetime import timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from smtplib import SMTPException, SMTP_SSL
from typing import Any, Callable

from django.utils import timezone

from portal.services.imap_common import decode_mime_header, normalize_attachment_filename
from portal.services.trade_calendar_service import count_trading_days_inclusive


def max_mail_job_trading_day_span() -> int:
    """读取环境变量 MAIL_JOB_MAX_TRADING_DAY_SPAN（默认 3）。

    语义：交易日个数上限，不是自然日。用于：
    - `validate_mail_job_query_span`：限制「查询日～运行日」闭区间内交易日个数；
    - 个别邮件任务（如 ZXDW）的 IMAP SINCE：自报告日向过去数若干个交易日确定检索起点。
    """
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


def csv_ext_ok(filename: str) -> bool:
    return os.path.splitext((filename or "").lower())[1] == ".csv"


def save_csv_attachments_from_rfc822(msg_bytes: bytes, save_dir: Path) -> list[Path]:
    """从整封邮件 RFC822 字节流中保存 .csv 附件（Content-Disposition 含 attachment）。"""
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
        if not csv_ext_ok(filename):
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


def rar_ext_ok(filename: str) -> bool:
    return os.path.splitext((filename or "").lower())[1] == ".rar"


def save_rar_attachments_from_rfc822(msg_bytes: bytes, save_dir: Path) -> list[Path]:
    """从整封邮件 RFC822 字节流中保存 .rar 附件。"""
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
        if not rar_ext_ok(filename):
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


def zip_ext_ok(filename: str) -> bool:
    return os.path.splitext((filename or "").lower())[1] == ".zip"


def excel_or_zip_ext_ok(filename: str) -> bool:
    _, ext = os.path.splitext((filename or "").lower())
    return ext in (".xlsx", ".xls", ".xlsm", ".zip")


def save_zip_attachments_from_rfc822(msg_bytes: bytes, save_dir: Path) -> list[Path]:
    """从整封邮件 RFC822 字节流中保存 .zip 附件。"""
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
        if not zip_ext_ok(filename):
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


def extract_zip_archive(zip_path: Path, dest_dir: Path) -> None:
    """解压 zip 到 dest_dir（兼容中文 Windows 压缩包文件名编码）。"""
    _extract_zip_preserving_cn_filenames(zip_path, dest_dir)


def _extract_zip_preserving_cn_filenames(zip_path: Path, dest_dir: Path) -> None:
    """
    解压 zip。中文 Windows 工具生成的压缩包常见「文件名 GBK、ZIP 内按 cp437 存」，
    不按 GBK 还原会得到乱码路径，后续找不到真正的 xlsx。
    Python 3.11+ 使用 ZipFile(metadata_encoding='gbk')；更早版本对 ZipInfo 做 cp437→gbk。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(zip_path, "r", metadata_encoding="gbk")
    except TypeError:
        zf = zipfile.ZipFile(zip_path, "r")
        for info in list(zf.infolist()):
            name = info.filename
            if name.endswith("/"):
                continue
            try:
                fixed = name.encode("cp437").decode("gbk")
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
            if fixed != name:
                info.filename = fixed
    with zf:
        zf.extractall(dest_dir)


def save_excel_zip_attachments_from_rfc822(msg_bytes: bytes, save_dir: Path) -> list[Path]:
    """
    保存 Excel 或 zip 附件；zip 解压到 save_dir/_zip_extract/<压缩包名>/，
    返回所有 Excel 路径（含 zip 内文件）。
    """
    msg = email.message_from_bytes(msg_bytes)
    save_dir.mkdir(parents=True, exist_ok=True)
    saved_files: list[Path] = []
    zips: list[Path] = []
    for part in msg.walk():
        disp = str(part.get("Content-Disposition", ""))
        if "attachment" not in disp.lower():
            continue
        filename_raw = part.get_filename()
        filename = normalize_attachment_filename(
            decode_mime_header(filename_raw) if filename_raw else ""
        )
        if not excel_or_zip_ext_ok(filename):
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
        saved_files.append(output)
        if output.suffix.lower() == ".zip":
            zips.append(output)

    extract_root = save_dir / "_zip_extract"
    excels: list[Path] = [p for p in saved_files if excel_ext_ok(p.name)]
    for zp in zips:
        dest = extract_root / zp.stem
        try:
            _extract_zip_preserving_cn_filenames(zp, dest)
        except zipfile.BadZipFile:
            continue
        for p in dest.rglob("*"):
            if p.is_file() and excel_ext_ok(p.name):
                excels.append(p)
    return excels


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


# --- MAIL_LOGS：命令在 stdout 末尾输出一行 JSON，供 alpha_mail_scheduler 解析落库（notify_snapshot）---

MAIL_LOG_RESULT_PREFIX = "__MAIL_LOG_RESULT_JSON__:"


def format_mail_job_notify_body(
    *,
    title: str,
    status: str,
    started_at,
    ended_at,
    base_url: str,
    field_rows: list[tuple[str, object]],
    extra_sections: list[str] | None = None,
) -> str:
    """统一结果通知邮件正文（纯文本）。"""
    duration = ended_at - started_at
    if isinstance(duration, timedelta):
        duration_sec = round(duration.total_seconds(), 3)
    else:
        duration_sec = 0.0
    lines: list[str] = [
        title,
        "",
        f"状态: {status}",
        f"开始时间: {timezone.localtime(started_at).strftime('%Y-%m-%d %H:%M:%S')}",
        f"结束时间: {timezone.localtime(ended_at).strftime('%Y-%m-%d %H:%M:%S')}",
        f"运行时长(秒): {duration_sec}",
        f"服务地址: {base_url or '-'}",
    ]
    for label, val in field_rows:
        if val is None or val == "":
            disp = "-"
        else:
            disp = val
        lines.append(f"{label}: {disp}")
    if extra_sections:
        for sec in extra_sections:
            if sec is None or not str(sec).strip():
                continue
            lines.append("")
            lines.extend(str(sec).strip().splitlines())
    return "\n".join(lines)


def mail_job_notify_base(
    *,
    notify_title: str,
    status: str,
    started_at,
    ended_at,
    base_url: str,
    data_import_succeeded: bool,
) -> dict[str, Any]:
    """构造写入 MAIL_LOGS.notify_snapshot 的公共字段（与邮件正文头部一致）。"""
    duration = ended_at - started_at
    if isinstance(duration, timedelta):
        duration_sec = round(duration.total_seconds(), 3)
    else:
        duration_sec = 0.0
    return {
        "notify_title": notify_title,
        "status": status,
        "started_at_local": timezone.localtime(started_at).strftime("%Y-%m-%d %H:%M:%S"),
        "ended_at_local": timezone.localtime(ended_at).strftime("%Y-%m-%d %H:%M:%S"),
        "duration_sec": duration_sec,
        "base_url": base_url or "",
        "data_import_succeeded": data_import_succeeded,
    }


def emit_mail_job_result_line(write: Callable[[str], None], payload: dict[str, Any]) -> None:
    """在 stdout 追加一行 JSON，供调度器写入 MAIL_LOGS.notify_snapshot（须为单行）。"""
    from portal.db.mongo import bson_safe_value

    safe: dict[str, Any] = {}
    for k, v in payload.items():
        safe[str(k)] = bson_safe_value(v)
    line = MAIL_LOG_RESULT_PREFIX + json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
    write(line + "\n")


def command_reported_result_email(notify_snapshot: dict[str, Any] | None) -> bool:
    """
    导入类命令在 _send_result_email 中已写出 notify_snapshot（并发送业务结果邮件）。
    调度器据此跳过重复的汇总邮件。
    """
    return bool(
        notify_snapshot and str(notify_snapshot.get("notify_title") or "").strip()
    )


def strip_mail_job_result_json(stdout_text: str) -> tuple[str, dict[str, Any] | None]:
    """
    从 stdout 剥离 MAIL_LOG JSON 行，返回 (剩余 stdout, 解析出的 dict)。
    自底向上查找带 __MAIL_LOG_RESULT_JSON__: 前缀的行（JSON 后可能还有「邮件已发送」等日志）。
    """
    text = stdout_text or ""
    marker = MAIL_LOG_RESULT_PREFIX
    lines = text.splitlines()
    json_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().startswith(marker):
            json_idx = i
            break
    if json_idx < 0:
        return text, None
    raw = lines[json_idx].strip()[len(marker) :]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return text, None
    rest_lines = lines[:json_idx] + lines[json_idx + 1 :]
    rest = "\n".join(rest_lines)
    if rest_lines and text.endswith("\n"):
        rest += "\n"
    return rest, data if isinstance(data, dict) else None
