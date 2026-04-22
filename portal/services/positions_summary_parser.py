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
        if first in ("品种", "Product") or first.startswith("Exchange"):
            continue
        if all((not x) for x in parts):
            continue
        if len(parts) < len(POSITIONS_COLS):
            parts.extend([""] * (len(POSITIONS_COLS) - len(parts)))
        elif len(parts) > len(POSITIONS_COLS):
            parts = parts[: len(POSITIONS_COLS)]

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
