"""吾执量化精选一号：fareport 对账单邮件中「资金、证券资产持有明细」→ position_fund_real.LHJX。"""
from __future__ import annotations

import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from portal.db.mongo import bson_safe_value

# 邮件主题（末尾为持仓日 YYYYMMDD，与对账期限一致）
LHJX_POSITION_MAIL_SUBJECT_PREFIX = (
    "0311020009225553上海吾执投资管理有限公司－吾执量化精选一号私募证券投资基金"
)


def build_lhjx_position_mail_subject(ymd8: str) -> str:
    day = (ymd8 or "").strip().replace("-", "")[:8]
    return f"{LHJX_POSITION_MAIL_SUBJECT_PREFIX}{day}"


def _norm_cell(v: Any) -> str:
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    s = str(v).strip().replace("\n", " ")
    return re.sub(r"\s+", " ", s)


def _parse_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "—", "－", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _six_digit_stock_code(raw: Any) -> str | None:
    if raw is None:
        return None
    try:
        if isinstance(raw, float) and pd.isna(raw):
            return None
    except Exception:
        pass
    s = str(raw).strip().replace(" ", "")
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    if re.fullmatch(r"\d{1,6}", s):
        return s.zfill(6)[-6:]
    m = re.search(r"(\d{6})", s)
    if m:
        return m.group(1)
    return None


def _exchange_prefix_from_market(market: str) -> str | None:
    m = _norm_cell(market)
    if not m:
        return None
    if "深圳" in m or "深市" in m or m.upper() == "SZ":
        return "SZ"
    if "上海" in m or "沪市" in m or m.upper() == "SH":
        return "SH"
    if "北京" in m or "京市" in m or m.upper() == "BJ":
        return "BJ"
    return None


def _infer_exchange_from_code(code6: str) -> str:
    """无「交易市场」列时的 A 股常见前缀推断。"""
    if not code6 or len(code6) != 6:
        return "SZ"
    if code6.startswith(("600", "601", "603", "605", "688", "689")):
        return "SH"
    if code6.startswith(("000", "001", "002", "003", "300", "301")):
        return "SZ"
    if code6.startswith(("43", "83", "87", "88")):
        return "BJ"
    if code6.startswith("6"):
        return "SH"
    return "SZ"


def normalize_position_stock_code(market_cell: Any, code_cell: Any) -> str | None:
    """统一为 SZ002008 / SH600660 / BJ430047 形式（code_nav 语义，字段名使用 `code`）。"""
    if code_cell is None:
        return None
    try:
        if isinstance(code_cell, float) and pd.isna(code_cell):
            return None
    except Exception:
        pass
    raw = str(code_cell).strip().upper().replace(" ", "")
    m_full = re.match(r"^(SZ|SH|BJ)(\d{6})$", raw)
    if m_full:
        return f"{m_full.group(1)}{m_full.group(2)}"

    code6 = _six_digit_stock_code(code_cell)
    if not code6:
        return None
    ex = _exchange_prefix_from_market(str(market_cell or ""))
    if not ex:
        ex = _infer_exchange_from_code(code6)
    return f"{ex}{code6}"


# 持仓明细表下方常见区块标题：出现即停止解析，避免把交易/流水表误当持仓
_LHJX_POSITION_SECTION_STOP_MARKERS: frozenset[str] = frozenset(
    ("交易明细", "流水明细", "人民币资金余额")
)


def _find_securities_detail_header_row(df: pd.DataFrame, *, max_scan: int = 45) -> int | None:
    """在原始表中定位「资金、证券资产持有明细」表头所在行索引。"""
    n = min(max_scan, len(df))
    for i in range(n):
        parts: list[str] = []
        for j in range(min(df.shape[1], 30)):
            parts.append(_norm_cell(df.iat[i, j]))
        joined = " ".join(parts)
        if "证券代码" in joined and ("持有数量" in joined or "市值" in joined):
            return i
    return None


def parse_lhjx_position_excel(
    file_bytes: bytes,
    *,
    filename: str,
    position_date_iso: str,
) -> list[dict[str, Any]]:
    """
    解析对账单 xlsx/xls 中证券持仓明细行。
    遇「交易明细」「流水明细」「人民币资金余额」等标题行即停止，不导入后续表格。
    输出文档字段：date（YYYY-MM-DD）、code（SZ/SH/BJ+6 位）、position_size（持有数量/股数）、
    position_value（市值）、avg_price（市值/数量，可空）、market、name、source_file。
    """
    _, ext = os.path.splitext((filename or "").lower())
    engine = "xlrd" if ext == ".xls" else "openpyxl"
    buf = BytesIO(file_bytes)
    raw = pd.read_excel(buf, header=None, dtype=object, engine=engine)
    if raw.empty:
        raise ValueError("Excel 无内容")

    hdr_i = _find_securities_detail_header_row(raw)
    if hdr_i is None:
        raise ValueError("未解析到「资金、证券资产持有明细」表头（需含证券代码、持有数量或市值）")

    header_vals = [raw.iat[hdr_i, j] for j in range(raw.shape[1])]
    col_names: list[str] = []
    used: dict[str, int] = {}
    for j, v in enumerate(header_vals):
        base = _norm_cell(v) or f"_col{j}"
        if base in used:
            used[base] += 1
            base = f"{base}_{used[base]}"
        else:
            used[base] = 0
        col_names.append(base)

    body = raw.iloc[hdr_i + 1 :].copy()
    body.columns = col_names[: body.shape[1]]
    # 只保留可能用到的列
    def _pick_col(candidates: tuple[str, ...]) -> str | None:
        for c in candidates:
            if c in body.columns:
                return c
        return None

    c_code = _pick_col(("证券代码",))
    c_qty = _pick_col(("持有数量",))
    c_mkt = _pick_col(("交易市场",))
    c_name = _pick_col(("证券简称",))
    c_mv = _pick_col(("市值",))
    c_cat = _pick_col(("资产类别", "证券类别"))

    if not c_code or not c_qty:
        raise ValueError("明细表缺少「证券代码」或「持有数量」列")

    pos_day = (position_date_iso or "").strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", pos_day):
        raise ValueError(f"position_date_iso 无效: {position_date_iso!r}")

    rows_out: list[dict[str, Any]] = []
    fname = os.path.basename(filename or "attachment.xlsx")

    for _, row in body.iterrows():
        row_joined = " ".join(
            _norm_cell(row.get(col)) for col in body.columns if col is not None
        )
        if any(m in row_joined for m in _LHJX_POSITION_SECTION_STOP_MARKERS):
            break

        code6_raw = row.get(c_code)
        if code6_raw is None or (isinstance(code6_raw, float) and pd.isna(code6_raw)):
            continue
        s_code = str(code6_raw).strip()
        if not s_code or s_code.lower() in ("nan",):
            continue
        if "合计" in s_code or s_code in ("-", "—"):
            continue

        cat = _norm_cell(row.get(c_cat)) if c_cat else ""
        if cat and "资金" in cat and "证券" not in cat and "股票" not in cat:
            continue

        mkt_cell = row.get(c_mkt) if c_mkt else ""
        unified = normalize_position_stock_code(mkt_cell, code6_raw)
        if not unified:
            continue

        qty = _parse_float(row.get(c_qty))
        if qty is None or qty <= 0:
            continue

        mv = _parse_float(row.get(c_mv)) if c_mv else None
        avg: float | None = None
        if mv is not None and qty > 0:
            avg = round(mv / qty, 6)

        name = _norm_cell(row.get(c_name)) if c_name else ""
        if "合计" in name:
            continue
        doc: dict[str, Any] = {
            "date": pos_day,
            "code": unified,
            "position_size": qty,
            "position_value": mv,
            "avg_price": avg,
            "market": _norm_cell(mkt_cell),
            "name": name,
            "source_file": fname,
        }
        for k, v in list(doc.items()):
            doc[k] = bson_safe_value(v)
        rows_out.append(doc)

    return rows_out


def _lhjx_position_xlsx_score(path: Path) -> int | None:
    """Excel 附件评分；非 Excel 返回 None。"""
    lower = path.name.lower()
    if not lower.endswith((".xlsx", ".xls", ".xlsm")):
        return None
    score = 0
    name = path.name
    if "0311020009225553" in name:
        score += 5
    if "T_0003" in name.upper() or "t_0003" in name:
        score += 3
    if "量化精选" in name:
        score += 2
    return score


def list_lhjx_position_xlsx_files(files: list[Path]) -> list[Path]:
    """返回全部可导入 Excel，按托管账户号/T_0003 等规则评分降序。"""
    scored: list[tuple[int, Path]] = []
    for p in files:
        s = _lhjx_position_xlsx_score(p)
        if s is not None:
            scored.append((s, p))
    scored.sort(key=lambda x: (-x[0], x[1].name))
    return [p for _, p in scored]


def pick_lhjx_position_xlsx(files: list[Path]) -> Path | None:
    """优先文件名含托管账户号 / T_0003 / 量化精选 的 Excel。"""
    ordered = list_lhjx_position_xlsx_files(files)
    return ordered[0] if ordered else None


def merge_lhjx_position_docs(
    doc_lists: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """多份 xlsx 解析结果按 code 合并，后出现的文件覆盖同 code。"""
    by_code: dict[str, dict[str, Any]] = {}
    for docs in doc_lists:
        for d in docs:
            code = str(d.get("code") or "").strip()
            if code:
                by_code[code] = d
    return list(by_code.values())
