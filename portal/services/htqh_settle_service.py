from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from portal.services.positions_summary_parser import parse_positions_summary

# 华泰期货结算单字段映射：英文键 -> 标签别名（支持大小写变体）
HTQH_FIELD_SPECS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("balance_bf", ("Balance B/F", "Balance b/f"), False),
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
    ("premium_received", ("Premium Received", "premium received"), False),
    ("premium_paid", ("Premium Paid", "premium paid"), False),
    ("delivery_pl", ("Delivery P/L",), False),
    ("initial_margin", ("Initial Margin",), False),
    ("balance_cf", ("Balance C/F", "Balance c/f"), False),
    ("pledge_amount", ("Pledge Amount",), False),
    ("client_equity", ("Client Equity",), False),
    ("fx_pledge_occ", ("FX Pledge Occ.", "FX Pledge Occ"), False),
    ("margin_occupied", ("Margin Occupied",), False),
    ("delivery_margin", ("Delivery Margin",), False),
    ("market_value_long", ("Market Value(long)", "Market value(long)"), False),
    ("market_value_short", ("Market Value(short)", "Market value(short)"), False),
    ("market_value_equity", ("Market Value(equity)", "Market value(equity)"), False),
    ("fund_avail", ("Fund Avail.", "Fund Avail"), False),
    ("risk_degree_pct", ("Risk Degree",), True),
    ("margin_call", ("Margin Call",), False),
    ("chg_in_fx_pledge", ("Chg in FX Pledge",), False),
)


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
        pattern = rf"{re.escape(alias)}[^\d+\-%]{{0,48}}([+-]?\d[\d,]*(?:\.\d+)?%?)"
        val = _extract_first(text, pattern)
        if val:
            return val
    return ""


def _extract_account_summary_metrics(text: str) -> dict[str, float | None]:
    out: dict[str, float | None] = {k: None for k, _, _ in HTQH_FIELD_SPECS}
    for key, aliases, is_percent in HTQH_FIELD_SPECS:
        raw = _extract_value_by_aliases(text, aliases)
        if not raw:
            continue
        try:
            out[key] = _parse_number(raw, is_percent=is_percent)
        except ValueError:
            out[key] = None
    return out


def _pick_target_txt_candidates(root: Path, account_id: str) -> list[Path]:
    txt_files = sorted(
        [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".txt"]
    )
    if not txt_files:
        raise RuntimeError("ZIP 解压后未找到 txt 文件。")
    # 账号优先
    account_hits = [p for p in txt_files if account_id in p.name]
    return account_hits or txt_files


def _pick_best_txt(root: Path, account_id: str) -> tuple[Path, str]:
    best_path: Path | None = None
    best_text = ""
    best_score = -1
    candidates = _pick_target_txt_candidates(root, account_id=account_id)

    for p in candidates:
        try:
            text = _read_text_with_fallbacks(p)
        except Exception:
            continue

        metrics = _extract_account_summary_metrics(text)
        metric_hits = sum(1 for v in metrics.values() if v is not None)
        low_name = p.name.lower()
        # 同分时偏向 otherfund，回避 trddata
        name_bonus = 2 if "otherfund" in low_name else 0
        name_penalty = -1 if "trddata" in low_name else 0
        score = metric_hits + name_bonus + name_penalty
        if score > best_score:
            best_score = score
            best_path = p
            best_text = text

    if not best_path:
        raise RuntimeError("ZIP 解压后的 txt 文件读取失败。")
    return best_path, best_text


def extract_htqh_record_from_zip(
    zip_path: Path,
    *,
    account_id: str,
    ymd: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="htqh_settle_") as tmp_dir:
        root = Path(tmp_dir)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(root)
        txt_path, text = _pick_best_txt(root, account_id=account_id)

    statement_ymd = _extract_first(text, r"\bDate[:：]\s*(\d{8})") or ymd
    client_id = _extract_first(text, r"\bClient ID[:：]\s*(\d+)")
    metrics = _extract_account_summary_metrics(text)
    positions = parse_positions_summary(text)
    return {
        "statement_ymd": statement_ymd,
        "client_id": client_id or account_id,
        "txt_file_name": txt_path.name,
        "metrics": metrics,
        "positions": positions,
    }
