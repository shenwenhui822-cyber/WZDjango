"""Alpha 股票目标持仓邮件：主题「股票持仓数据{YYYYMMDD}」+ 附件 alpha_{YYYYMMDD}.zip 内多份 CSV。"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd

from portal.db.mongo import bson_safe_value

_ALPHA_ZIP_RE = re.compile(r"^alpha_(\d{8})\.zip$", re.IGNORECASE)
_TABLE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


def build_alpha_target_position_subject(ymd8: str) -> str:
    prefix = (
        os.getenv("ALPHA_TARGET_POSITION_MAIL_SUBJECT_PREFIX") or "股票持仓数据"
    ).strip()
    return f"{prefix}{ymd8}"


def pick_alpha_target_zip(files: list[Path], ymd8: str) -> Path | None:
    """优先 alpha_{ymd8}.zip，否则取唯一 zip 或首个匹配日期的 zip。"""
    if not files:
        return None
    exact = [p for p in files if p.name.lower() == f"alpha_{ymd8}.zip"]
    if len(exact) == 1:
        return exact[0]
    dated = [
        p
        for p in files
        if (m := _ALPHA_ZIP_RE.match(p.name)) and m.group(1) == ymd8
    ]
    if len(dated) == 1:
        return dated[0]
    if len(files) == 1:
        return files[0]
    return dated[0] if dated else files[0]


def collection_name_from_csv(path: Path) -> str | None:
    name = path.stem.strip()
    if _TABLE_NAME_RE.fullmatch(name):
        return name
    return None


def list_csv_in_extract_dir(extract_root: Path) -> list[Path]:
    if not extract_root.is_dir():
        return []
    return sorted(
        p for p in extract_root.rglob("*.csv") if p.is_file() and collection_name_from_csv(p)
    )


def parse_alpha_target_csv(path: Path, *, date_iso: str) -> list[dict]:
    """解析单份 CSV：列 ticker、lots，附加 date。"""
    last_err: Exception | None = None
    df = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            df = pd.read_csv(path, encoding=enc, dtype=str)
            break
        except Exception as exc:
            last_err = exc
    if df is None:
        raise RuntimeError(f"无法读取 CSV {path.name}: {last_err}")

    col_map = {str(c).strip().lower(): c for c in df.columns}
    ticker_key = col_map.get("ticker")
    lots_key = col_map.get("lots")
    if not ticker_key or not lots_key:
        raise ValueError(
            f"{path.name} 缺少 ticker/lots 列，当前列: {list(df.columns)}"
        )

    docs: list[dict] = []
    for _, row in df.iterrows():
        ticker_raw = row.get(ticker_key)
        if pd.isna(ticker_raw) or str(ticker_raw).strip() == "":
            continue
        ticker = str(ticker_raw).strip()
        lots_raw = row.get(lots_key)
        if pd.isna(lots_raw) or str(lots_raw).strip() == "":
            continue
        try:
            lots = int(float(str(lots_raw).replace(",", "").strip()))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path.name} ticker={ticker!r} lots 无法解析: {lots_raw!r}") from exc
        docs.append(
            {
                "date": date_iso,
                "ticker": ticker,
                "lots": lots,
            }
        )
    return [d for d in docs if bson_safe_value(d.get("ticker"))]
