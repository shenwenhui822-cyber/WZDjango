from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from portal.services.positions_summary_parser import parse_positions_summary


ACCOUNT_SUMMARY_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("balance_bf", "Balance b/f", False),
    ("deposit_withdrawal", "Deposit/Withdrawal", False),
    ("realized_pl", "Realized P/L", False),
    ("mtm_pl", "MTM P/L", False),
    ("exercise_pl", "Exercise P/L", False),
    ("commission", "Commission", False),
    ("exercise_fee", "Exercise Fee", False),
    ("delivery_fee", "Delivery Fee", False),
    ("new_fx_pledge", "New FX Pledge", False),
    ("fx_redemption", "FX Redemption", False),
    ("chg_in_pledge_amt", "Chg in Pledge Amt", False),
    ("premium_received", "Premium received", False),
    ("premium_paid", "Premium paid", False),
    ("chg_in_fx_pledge", "Chg in FX Pledge", False),
    ("initial_margin", "Initial Margin", False),
    ("balance_cf", "Balance c/f", False),
    ("pledge_amount", "Pledge Amount", False),
    ("client_equity", "Client Equity", False),
    ("fx_pledge_occ", "FX Pledge Occ", False),
    ("margin_occupied", "Margin Occupied", False),
    ("delivery_margin", "Delivery Margin", False),
    ("market_value_long", "Market value(long)", False),
    ("market_value_short", "Market value(short)", False),
    ("market_value_equity", "Market value(equity)", False),
    ("fund_avail", "Fund Avail.", False),
    ("risk_degree_pct", "Risk Degree", True),
    ("margin_call", "Margin Call", False),
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


def _pick_statement_txt(root: Path, *, account_id: str, ymd: str) -> Path:
    txt_files = sorted(root.rglob("*.txt"))
    if not txt_files:
        raise RuntimeError("RAR 解压后未找到 txt 文件。")

    preferred = [
        p
        for p in txt_files
        if account_id in p.name and ymd in p.name and "Settlement Statement" in p.name
    ]
    if preferred:
        return preferred[0]

    fallback = [p for p in txt_files if account_id in p.name and ymd in p.name]
    if fallback:
        return fallback[0]
    return txt_files[0]


def _read_text_with_fallbacks(path: Path) -> str:
    for encoding in ("gbk", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"无法读取结算单文本编码: {path.name}")


def _parse_numeric(raw: str, *, percent: bool) -> float:
    s = (raw or "").strip().replace(",", "")
    if percent:
        s = s.replace("%", "")
    return float(s)


def _extract_first(text: str, pattern: str) -> str:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return m.group(1).strip() if m else ""


def parse_account_summary_metrics(statement_text: str) -> dict[str, float | None]:
    out: dict[str, float | None] = {k: None for k, _, _ in ACCOUNT_SUMMARY_FIELDS}
    for key, label, is_percent in ACCOUNT_SUMMARY_FIELDS:
        patt = rf"{re.escape(label)}\s*[:：.]?\s*([+-]?\d[\d,]*(?:\.\d+)?%?)"
        matched = _extract_first(statement_text, patt)
        if not matched:
            continue
        try:
            out[key] = _parse_numeric(matched, percent=is_percent)
        except ValueError:
            out[key] = None
    return out


def extract_settle_record_from_rar(
    rar_path: Path,
    *,
    account_id: str,
    ymd: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="wkqh_settle_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        _run_extract_command(rar_path, tmp_root)
        txt_path = _pick_statement_txt(tmp_root, account_id=account_id, ymd=ymd)
        text = _read_text_with_fallbacks(txt_path)

    statement_ymd = _extract_first(text, r"\bDate[:：]\s*(\d{8})") or ymd
    client_id = _extract_first(text, r"\bClient ID[:：]\s*(\d+)")
    metrics = parse_account_summary_metrics(text)
    positions = parse_positions_summary(text)
    return {
        "statement_ymd": statement_ymd,
        "client_id": client_id or account_id,
        "txt_file_name": txt_path.name,
        "metrics": metrics,
        "positions": positions,
    }
