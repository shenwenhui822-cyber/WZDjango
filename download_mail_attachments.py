#!/usr/bin/env python3
"""
独立脚本：按自定义主题从 IMAP 邮箱下载附件到指定目录。

修改下方「配置区」后直接运行：
  python download_mail_attachments.py

不依赖本项目其他 Python 模块，仅使用标准库。
"""
from __future__ import annotations

import email
import imaplib
import re
from datetime import date, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

# =============================================================================
# 配置区（按需修改）
# =============================================================================

# 登录账户：alpha / wangkan / farport
ACCOUNT = "wangkan"

# 邮件主题（IMAP 头 Subject，不是正文「转发的邮件信息」里的主题）
SUBJECT = "吾执 2026-06-05"

# 主题匹配方式：contains / exact / startswith
MATCH_MODE = "exact"

# 去掉转发前缀后再比主题（转发：/Fwd:/FW:/Re: 等）
STRIP_FORWARD_PREFIX = True

# 发件人过滤（子串，不区分大小写）；留空不限制
FROM_FILTER = ""

# 附件保存目录
OUTPUT_DIR = r"G:\github\WZDjango\date\20260608"

# .env 路径；留空则使用项目根目录 .env
ENV_FILE = ""

# 指定邮件 ID；留空则自动选匹配主题的最新一封
MAIL_ID = ""

# 只保存指定后缀，逗号分隔；留空表示保存全部附件
EXT_FILTER = ""

# True=仅列出匹配邮件，不下载
LIST_ONLY = False

# 搜索时最多返回多少封匹配邮件
SEARCH_LIMIT = 5

# IMAP 搜索范围：仅扫描最近 N 天邮件（0=从主题末尾 YYYYMMDD 自动推算，否则用此天数）
SEARCH_SINCE_DAYS = 0

# =============================================================================


def _script_root() -> Path:
    return Path(__file__).resolve().parent


def load_env_file(env_path: Path) -> dict[str, str]:
    """简单解析 KEY=VALUE 行，忽略 # 注释与空行。"""
    env: dict[str, str] = {}
    if not env_path.is_file():
        raise FileNotFoundError(f"未找到 .env 文件: {env_path}")
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


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


def normalize_filename(name: str) -> str:
    clean = (name or "").strip().replace("\r", "").replace("\n", "")
    clean = re.sub(r'[<>:"/\\|?*]', "_", clean)
    return clean or "attachment.bin"


def resolve_account(env: dict[str, str], account: str) -> tuple[str, str, str, int]:
    account = account.strip().lower()
    imap_server = env.get("ALPHA_IMAP_SERVER", "imap.exmail.qq.com")
    imap_port = int(env.get("ALPHA_IMAP_PORT", "993"))

    if account in ("alpha", "wangkan", "1"):
        user = env.get("ALPHA_MAIL_USER", "")
        password = env.get("ALPHA_MAIL_PASS", "")
        label = "alpha (ALPHA_MAIL_*)"
    elif account in ("farport", "fareport", "2"):
        user = env.get("FARPORT_MAIL_USER", "")
        password = env.get("FARPORT_MAIL_PASS", "")
        label = "farport (FARPORT_MAIL_*)"
    else:
        raise ValueError(f"未知账户: {account!r}，请在配置区设置 alpha 或 farport")

    if not user or not password:
        raise ValueError(f"{label} 的用户名或密码为空，请检查 .env")

    return user, password, imap_server, imap_port


def normalize_subject(value: str) -> str:
    """统一破折号、去首尾空白，便于与邮箱解码后的主题比较。"""
    s = (value or "").strip()
    for ch in ("－", "—", "–", "−"):
        s = s.replace(ch, "-")
    # 合并连续空白
    s = re.sub(r"\s+", " ", s)
    return s


_FORWARD_PREFIX_RE = re.compile(
    r"^(?:(?:转发|回复|答复|re|fw|fwd)\s*[:：]\s*)+",
    re.IGNORECASE,
)


def strip_forward_subject_prefix(value: str) -> str:
    s = normalize_subject(value)
    while True:
        nxt = _FORWARD_PREFIX_RE.sub("", s, count=1).strip()
        if nxt == s:
            return s
        s = nxt


def extract_date_from_subject(text: str) -> date | None:
    s = (text or "").strip()
    m = re.search(r"(\d{8})\s*$", s)
    if m:
        ymd8 = m.group(1)
        try:
            return date(int(ymd8[:4]), int(ymd8[4:6]), int(ymd8[6:8]))
        except ValueError:
            pass
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*$", s)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return None


def extract_trailing_ymd8(text: str) -> str | None:
    d = extract_date_from_subject(text)
    return d.strftime("%Y%m%d") if d else None


def _imap_en_month_date(d: date) -> str:
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{d.day}-{months[d.month - 1]}-{d.year}"


def resolve_search_since(subject_pattern: str) -> date | None:
    """缩小 IMAP 扫描范围，避免全箱逐封 fetch 过慢。"""
    if SEARCH_SINCE_DAYS and SEARCH_SINCE_DAYS > 0:
        return date.today() - timedelta(days=SEARCH_SINCE_DAYS)
    d = extract_date_from_subject(subject_pattern)
    if d:
        return d - timedelta(days=7)
    return date.today() - timedelta(days=30)


def subject_for_match(subject: str) -> str:
    s = normalize_subject(subject)
    if STRIP_FORWARD_PREFIX:
        s = strip_forward_subject_prefix(s)
    return s


def subject_matches(subject: str, pattern: str, match_mode: str) -> bool:
    subj = subject_for_match(subject)
    pat = subject_for_match(pattern)
    if not pat:
        return False
    if match_mode == "exact":
        return subj == pat
    if match_mode == "startswith":
        return subj.startswith(pat)
    return pat in subj


def from_matches(sender: str, pattern: str) -> bool:
    pat = (pattern or "").strip().lower()
    if not pat:
        return True
    return pat in (sender or "").lower()


def open_inbox(user: str, password: str, host: str, port: int) -> imaplib.IMAP4_SSL:
    mailbox = imaplib.IMAP4_SSL(host, port)
    mailbox.login(user, password)
    status, _ = mailbox.select("INBOX")
    if status != "OK":
        raise RuntimeError("无法打开 INBOX")
    return mailbox


def list_mail_ids(
    mailbox: imaplib.IMAP4_SSL,
    *,
    since: date | None = None,
) -> list[bytes]:
    if since is not None:
        crit = f'SINCE "{_imap_en_month_date(since)}"'
    else:
        crit = "ALL"
    status, data = mailbox.search(None, crit)
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def fetch_subject_from_and_date(
    mailbox: imaplib.IMAP4_SSL, mail_id: bytes | str
) -> tuple[str, str, object | None]:
    mid = mail_id.decode() if isinstance(mail_id, bytes) else str(mail_id)
    status, msg_data = mailbox.fetch(mid, "(BODY[HEADER.FIELDS (SUBJECT FROM DATE)])")
    if status != "OK" or not msg_data:
        return "", "", None
    header_b: bytes | None = None
    for chunk in msg_data:
        if isinstance(chunk, tuple) and len(chunk) >= 2 and isinstance(
            chunk[1], (bytes, bytearray)
        ):
            header_b = bytes(chunk[1])
            break
    if not header_b:
        return "", "", None
    msg = email.message_from_bytes(header_b)
    subject = decode_mime_header(msg.get("Subject", "")).strip()
    sender = decode_mime_header(msg.get("From", "")).strip()
    raw_date = msg.get("Date", "")
    try:
        dt = parsedate_to_datetime(raw_date)
    except Exception:
        dt = None
    return subject, sender, dt


def find_matching_mails(
    mailbox: imaplib.IMAP4_SSL,
    subject_pattern: str,
    match_mode: str,
    limit: int = 20,
    *,
    since: date | None = None,
) -> list[dict]:
    mail_ids = list_mail_ids(mailbox, since=since)
    total = len(mail_ids)
    since_text = since.isoformat() if since else "全部"
    print(f"扫描邮件: {total} 封（SINCE {since_text}，从新到旧）", flush=True)

    matches: list[dict] = []
    scanned = 0
    for mail_id in reversed(mail_ids):
        scanned += 1
        if scanned == 1 or scanned % 100 == 0 or scanned == total:
            print(f"  已检查 {scanned}/{total} …", flush=True)
        subject, sender, dt = fetch_subject_from_and_date(mailbox, mail_id)
        if not from_matches(sender, FROM_FILTER):
            continue
        if not subject_matches(subject, subject_pattern, match_mode):
            continue
        matches.append(
            {
                "id": mail_id.decode() if isinstance(mail_id, bytes) else str(mail_id),
                "subject": subject,
                "from": sender,
                "date": dt,
            }
        )
        if len(matches) >= limit:
            break
    print(f"扫描完成，匹配 {len(matches)} 封", flush=True)
    return matches


def pick_latest_match(matches: list[dict]) -> dict | None:
    if not matches:
        return None
    best = matches[0]
    best_dt = best.get("date")
    for item in matches[1:]:
        dt = item.get("date")
        if dt is not None and (best_dt is None or dt > best_dt):
            best = item
            best_dt = dt
    return best


def fetch_rfc822(mailbox: imaplib.IMAP4_SSL, mail_id: str) -> bytes:
    status, msg_data = mailbox.fetch(mail_id.encode(), "(RFC822)")
    if status != "OK" or not msg_data or not msg_data[0]:
        raise RuntimeError(f"无法读取邮件 {mail_id}")
    raw = msg_data[0][1]
    if not isinstance(raw, (bytes, bytearray)):
        raise RuntimeError("邮件内容格式异常")
    return bytes(raw)


def unique_output_path(output_dir: Path, filename: str) -> Path:
    output = output_dir / filename
    if not output.exists():
        return output
    stem, suffix = output.stem, output.suffix
    i = 1
    while True:
        candidate = output_dir / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def save_attachments(
    msg_bytes: bytes,
    output_dir: Path,
    *,
    ext_filter: str = "",
) -> list[Path]:
    msg = email.message_from_bytes(msg_bytes)
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    allowed_exts = {
        x.strip().lower().lstrip(".")
        for x in ext_filter.split(",")
        if x.strip()
    }

    for part in msg.walk():
        disp = str(part.get("Content-Disposition", ""))
        filename_raw = part.get_filename()
        if "attachment" not in disp.lower() and not filename_raw:
            continue
        if not filename_raw:
            continue

        filename = normalize_filename(decode_mime_header(filename_raw))
        ext = Path(filename).suffix.lower().lstrip(".")
        if allowed_exts and ext not in allowed_exts:
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        output = unique_output_path(output_dir, filename)
        output.write_bytes(payload)
        saved.append(output)

    return saved


def main() -> int:
    if not SUBJECT.strip():
        print("配置错误: SUBJECT 不能为空")
        return 1
    if not OUTPUT_DIR.strip():
        print("配置错误: OUTPUT_DIR 不能为空")
        return 1

    env_path = Path(ENV_FILE).expanduser().resolve() if ENV_FILE.strip() else _script_root() / ".env"
    env = load_env_file(env_path)
    user, password, imap_host, imap_port = resolve_account(env, ACCOUNT)
    output_dir = Path(OUTPUT_DIR).expanduser().resolve()

    print(f"账户: {ACCOUNT}", flush=True)
    print(f"主题: {SUBJECT}（match={MATCH_MODE}）", flush=True)
    print(f"保存目录: {output_dir}", flush=True)
    print(f".env: {env_path}", flush=True)

    mailbox: imaplib.IMAP4_SSL | None = None
    try:
        print(f"IMAP 登录: {user} @ {imap_host}:{imap_port}", flush=True)
        mailbox = open_inbox(user, password, imap_host, imap_port)

        if MAIL_ID.strip():
            selected = {"id": MAIL_ID.strip(), "subject": "(指定 MAIL_ID)", "date": None}
            matches = [selected]
        else:
            since = resolve_search_since(SUBJECT)
            fetch_limit = max(SEARCH_LIMIT, 1) if LIST_ONLY else 1
            matches = find_matching_mails(
                mailbox,
                SUBJECT,
                MATCH_MODE,
                limit=fetch_limit,
                since=since,
            )
            if not matches:
                print(f"未找到匹配主题的邮件: {SUBJECT!r}（match={MATCH_MODE}）")
                print("常见原因：")
                print("  1. 网页看到的主题可能是正文里「转发的邮件信息」，IMAP 匹配的是邮件头 Subject")
                print("  2. 该邮件可能不在当前登录邮箱（TraderYvonne 仓单常在 baichunkai，不在 wangkan）")
                print("  3. MATCH_MODE=exact 时主题须与 IMAP 头完全一致；可设 STRIP_FORWARD_PREFIX=True")
                print("  4. 主题末尾日期格式：2026-06-05 或 20260605")
                print(
                    f"  诊断：已对 {user} INBOX 全量扫描，"
                    "无 traderyvonne@163.com 且主题为「吾执 YYYY-MM-DD」的邮件"
                )
                return 1

            print(f"找到 {len(matches)} 封匹配邮件：")
            for i, item in enumerate(matches, 1):
                dt = item.get("date")
                dt_text = dt.isoformat(sep=" ", timespec="seconds") if dt else "—"
                print(f"  [{i}] id={item['id']}  date={dt_text}  subject={item['subject']}")

            if LIST_ONLY:
                return 0

            selected = pick_latest_match(matches)
            if selected is None:
                print("未选中任何邮件")
                return 1

        print(f"使用邮件 id={selected['id']}")
        raw = fetch_rfc822(mailbox, selected["id"])
        saved = save_attachments(raw, output_dir, ext_filter=EXT_FILTER)

        if not saved:
            print("该邮件没有可保存的附件（或被 EXT_FILTER 过滤）")
            return 1

        print(f"已保存 {len(saved)} 个附件到 {output_dir}:")
        for path in saved:
            print(f"  - {path.name} ({path.stat().st_size} bytes)")
        return 0
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
