from __future__ import annotations

from typing import Any

POSITIONS_COLS: tuple[str, ...] = (
    "product",
    "instrument",
    "long_pos",
    "avg_buy_price",
    "short_pos",
    "avg_sell_price",
    "prev_sttl",
    "sttl_today",
    "mtm_pl",
    "margin_occupied",
    "s_h",
    "market_value_long",
    "market_value_short",
)

POSITION_NUM_COLS: frozenset[str] = frozenset(
    {
        "long_pos",
        "avg_buy_price",
        "short_pos",
        "avg_sell_price",
        "prev_sttl",
        "sttl_today",
        "mtm_pl",
        "margin_occupied",
        "market_value_long",
        "market_value_short",
    }
)


def _to_float_or_none(v: str) -> float | None:
    s = (v or "").strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _is_header_row(parts: list[str]) -> bool:
    p0 = (parts[0] if parts else "").strip().lower()
    p1 = (parts[1] if len(parts) > 1 else "").strip().lower()
    p2 = (parts[2] if len(parts) > 2 else "").strip().lower()
    p3 = (parts[3] if len(parts) > 3 else "").strip().lower()
    header_tokens = {
        "投资单元",
        "investunit",
        "交易编码",
        "tradingcode",
        "品种",
        "product",
        "合约",
        "instrument",
        "买持",
        "long pos.",
    }
    return (p0 in header_tokens) or (p1 in header_tokens) or (p2 in header_tokens) or (p3 in header_tokens)


def _normalize_parts(parts: list[str]) -> list[str]:
    first = (parts[0] if parts else "").strip()
    # 华泰/部分期货结算单是 16 列（前两列为投资单元、交易编码），需投影到统一 13 列结构。
    if len(parts) >= 15:
        if first.startswith("共"):
            return [
                parts[0],  # product
                "",  # instrument
                parts[4],  # long_pos
                parts[5],  # avg_buy_price
                parts[6],  # short_pos
                parts[7],  # avg_sell_price
                parts[8],  # prev_sttl
                parts[9],  # sttl_today
                parts[10],  # mtm_pl
                parts[11],  # margin_occupied
                parts[12],  # s_h
                parts[13],  # market_value_long
                parts[14],  # market_value_short
            ]
        return [
            parts[2],  # product
            parts[3],  # instrument
            parts[4],  # long_pos
            parts[5],  # avg_buy_price
            parts[6],  # short_pos
            parts[7],  # avg_sell_price
            parts[8],  # prev_sttl
            parts[9],  # sttl_today
            parts[10],  # mtm_pl
            parts[11],  # margin_occupied
            parts[12],  # s_h
            parts[13],  # market_value_long
            parts[14],  # market_value_short
        ]
    return parts


def parse_positions_summary(
    text: str,
    *,
    stop_keywords: tuple[str, ...] = ("能源中心", "尊敬的客户", "尊敬的投资者", "公司盖章"),
) -> dict[str, Any]:
    lines = text.splitlines()
    start_idx = -1
    for i, ln in enumerate(lines):
        if "持仓汇总" in ln and "Positions" in ln:
            start_idx = i
            break
    if start_idx < 0:
        return {"rows": [], "total": None}

    rows: list[dict[str, Any]] = []
    total: dict[str, Any] | None = None
    for ln in lines[start_idx + 1 :]:
        t = ln.strip()
        if not t:
            if rows or total is not None:
                break
            continue
        if any(k in t for k in stop_keywords):
            break
        if "|" not in ln:
            continue
        parts = [p.strip() for p in ln.strip().strip("|").split("|")]
        if len(parts) < 2:
            continue
        first = parts[0]
        if _is_header_row(parts):
            continue
        if all((not x) for x in parts):
            continue
        parts = _normalize_parts(parts)
        if len(parts) < len(POSITIONS_COLS):
            parts.extend([""] * (len(POSITIONS_COLS) - len(parts)))
        elif len(parts) > len(POSITIONS_COLS):
            parts = parts[: len(POSITIONS_COLS)]

        first = parts[0] if parts else ""
        row: dict[str, Any] = {}
        for k, val in zip(POSITIONS_COLS, parts):
            if k in POSITION_NUM_COLS:
                row[k] = _to_float_or_none(val)
            else:
                row[k] = (val or "").strip() or None
        if first.startswith("共"):
            total = row
        else:
            rows.append(row)
    return {"rows": rows, "total": total}
