from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


# 长江期货结算单（81801575）字段映射：英文键 -> 结算单英文标签（支持别名）
CJQH_FIELD_SPECS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("balance_bf", ("Balance b/f",), False),
    ("deposit_withdrawal", ("Deposit/Withdrawal",), False),
    ("realized_pl", ("Realized P/L",), False),
    ("mtm_pl", ("MTM P/L",), False),
    ("exercise_pl", ("Exercise P/L",), False),
    ("commission", ("Commission",), False),
    ("exercise_fee", ("Exercise Fee",), False),
    ("delivery_fee", ("Delivery Fee",), False),
    ("new_fx_pledge", ("New FX Pledge",), False),
    ("fx_redemption", ("FX Redemption",), False),
    ("chg_in_pledge_amt", ("Chg in Pledge Amt",), False),
    ("premium_received", ("premium received", "Premium received"), False),
    ("premium_paid", ("premium paid", "Premium paid"), False),
    ("initial_margin", ("Initial Margin",), False),
    ("balance_cf", ("Balance c/f",), False),
    ("pledge_amount", ("Pledge Amount",), False),
    ("client_equity", ("Client Equity",), False),
    ("fx_pledge_occ", ("FX Pledge Occ.", "FX Pledge Occ"), False),
    ("margin_occupied", ("Margin Occupied",), False),
    ("delivery_margin", ("Delivery Margin",), False),
    ("market_value_long", ("market value(long)", "Market value(long)"), False),
    ("market_value_short", ("market value(short)", "Market value(short)"), False),
    ("market_value_equity", ("market value(equity)", "Market value(equity)"), False),
    ("fund_avail", ("Fund Avail.", "Fund Avail"), False),
    ("risk_degree_pct", ("Risk Degree",), True),
    ("margin_call", ("Margin Call",), False),
)


def _run_extract_command(rar_path: Path, output_dir: Path) -> None:
    commands: list[list[str]] = []
    if shutil.which("unrar"):
        commands.append(["unrar", "x", "-o+", str(rar_path), str(output_dir)])
    if shutil.which("7z"):
        commands.append(["7z", "x", "-y", f"-o{output_dir}", str(rar_path)])
    if shutil.which("7za"):
        commands.append(["7za", "x", "-y", f"-o{output_dir}", str(rar_path)])
    if shutil.which("bsdtar"):
        commands.append(["bsdtar", "-xf", str(rar_path), "-C", str(output_dir)])
    if shutil.which("unar"):
        commands.append(["unar", "-o", str(output_dir), str(rar_path)])
    if not commands:
        raise RuntimeError("未找到可用解压命令（unrar/7z/7za/bsdtar/unar）。")

    last_err = ""
    for cmd in commands:
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return
        except subprocess.CalledProcessError as exc:
            msg = (exc.stderr or b"").decode("utf-8", errors="ignore")
            last_err = f"{' '.join(cmd[:2])} 失败: {msg or exc}"
    raise RuntimeError(last_err or "RAR 解压失败。")


def _read_text_with_fallbacks(path: Path) -> str:
    for encoding in ("gbk", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"无法读取结算单文本编码: {path.name}")


def _extract_first(text: str, pattern: str) -> str:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _parse_number(raw: str, *, is_percent: bool) -> float:
    s = (raw or "").strip().replace(",", "")
    if is_percent:
        s = s.replace("%", "")
    return float(s)


def _extract_value_by_aliases(text: str, aliases: tuple[str, ...]) -> str:
    for alias in aliases:
        # 兼容标签与数值之间存在中文说明、全角冒号、额外空格等情况
        pattern = rf"{re.escape(alias)}[^\d+\-%]{{0,48}}([+-]?\d[\d,]*(?:\.\d+)?%?)"
        val = _extract_first(text, pattern)
        if val:
            return val
    return ""


def _extract_account_summary_metrics(text: str) -> dict[str, float | None]:
    out: dict[str, float | None] = {k: None for k, _, _ in CJQH_FIELD_SPECS}
    for key, aliases, is_percent in CJQH_FIELD_SPECS:
        raw = _extract_value_by_aliases(text, aliases)
        if not raw:
            continue
        try:
            out[key] = _parse_number(raw, is_percent=is_percent)
        except ValueError:
            out[key] = None
    return out


def _pick_target_txt(root: Path, account_id: str) -> Path:
    txt_files = sorted(
        [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".txt"]
    )
    if not txt_files:
        raise RuntimeError("RAR 解压后未找到 txt 文件。")

    # 1) 优先命中主结算单：81801575.TXT（忽略大小写）
    exact_name = f"{account_id}.txt".lower()
    for p in txt_files:
        if p.name.lower() == exact_name:
            return p

    # 2) 次优先：文件名含 account/summary/statement（排除成交明细 trade）
    summary_hits = []
    for p in txt_files:
        low = p.name.lower()
        if account_id in low and any(k in low for k in ("account", "summary", "statement")) and "trade" not in low:
            summary_hits.append(p)
    if summary_hits:
        return summary_hits[0]

    # 3) 再次优先：只要包含账号，仍优先非 trade 文件
    account_hits = [p for p in txt_files if account_id in p.name]
    if account_hits:
        non_trade = [p for p in account_hits if "trade" not in p.name.lower()]
        if non_trade:
            return non_trade[0]
        return account_hits[0]

    return txt_files[0]


def extract_cjqh_record_from_rar(
    rar_path: Path,
    *,
    account_id: str,
    ymd: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="cjqh_settle_") as tmp_dir:
        root = Path(tmp_dir)
        _run_extract_command(rar_path, root)
        txt_path = _pick_target_txt(root, account_id=account_id)
        text = _read_text_with_fallbacks(txt_path)

    statement_ymd = _extract_first(text, r"\bDate[:：]\s*(\d{8})") or ymd
    client_id = _extract_first(text, r"\bClient ID[:：]\s*(\d+)")
    metrics = _extract_account_summary_metrics(text)
    return {
        "statement_ymd": statement_ymd,
        "client_id": client_id or account_id,
        "txt_file_name": txt_path.name,
        "metrics": metrics,
    }
