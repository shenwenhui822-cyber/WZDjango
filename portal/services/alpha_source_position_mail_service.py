"""吾执 Alpha 源持仓：wangkan 邮件主题「吾执 YYYY-MM-DD」zip 附件内 CSV → position_alpha_source.<表名>。"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import pandas as pd
from django.utils import timezone

from portal.db.mongo import bson_safe_value, get_alpha_source_position_collection
from portal.services.mail_import_common import extract_zip_archive

# CSV 文件名后缀（YYYYMMDD-positions-{suffix}.csv）→ Mongo 集合名
ALPHA_SOURCE_CSV_SUFFIX_TO_TABLE: dict[str, str] = {
    "赋源1000指增-中证1000指增标准版-华泰-普通": "FY1000ZZ",
    "吾执博士一号-中证1000指增标准版-华泰-普通": "WZBSYH",
    "吾执多元量选-量化选股标准版": "WZDYLX",
    "吾执多元一号-中证1000指增权重股版-东吴-普通": "WZDYYH",
    "吾执二二号-中证1000指增权重股版": "WZEEH",
    "吾执量化精选二号-量化选股标准版-东方-普通": "WZLHJXEH",
    "吾执量化精选一号-量化选股标准版": "WZLHJXYH",
    "吾执三零号-中证1000指增权重股版": "WZSLH",
    "吾执一三号-量化选股标准版-浙商-信用": "WZYSH",
    "吾执泽鑫多维-中证1000指增权重股版-国海-信用": "WZZXDW",
}

_CSV_NAME_RE = re.compile(
    r"^(?P<ymd8>\d{8})-positions-(?P<suffix>.+)\.csv$",
    re.IGNORECASE,
)
_ZIP_DATE_RE = re.compile(r"(\d{8})|(\d{4}-\d{2}-\d{2})")


def build_alpha_source_mail_subject(position_date_iso: str) -> str:
    """邮件主题：吾执 YYYY-MM-DD。"""
    iso = (position_date_iso or "").strip()[:10]
    return f"吾执 {iso}"


def table_name_for_csv_filename(filename: str) -> str | None:
    """从 CSV 文件名解析 Mongo 集合名；无法识别时返回 None。"""
    name = Path(filename or "").name.strip()
    m = _CSV_NAME_RE.match(name)
    if not m:
        return None
    suffix = m.group("suffix").strip()
    return ALPHA_SOURCE_CSV_SUFFIX_TO_TABLE.get(suffix)


def ymd8_from_csv_filename(filename: str) -> str | None:
    name = Path(filename or "").name.strip()
    m = _CSV_NAME_RE.match(name)
    return m.group("ymd8") if m else None


def iso_from_ymd8(ymd8: str) -> str:
    s = (ymd8 or "").strip()[:8]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def position_date_iso_from_name(text: str) -> str | None:
    """从 zip/邮件文件名或主题中解析 YYYY-MM-DD。"""
    for m in _ZIP_DATE_RE.finditer(text or ""):
        if m.group(1):
            return iso_from_ymd8(m.group(1))
        if m.group(2):
            return m.group(2)
    return None


def _csv_paths_in_dir(extract_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in extract_dir.rglob("*.csv")
        if "__MACOSX" not in p.parts and not p.name.startswith("._")
    )


def _resolve_position_date_iso(
    *,
    csv_paths: list[Path],
    position_date_iso: str | None,
    fallback_name: str,
) -> str | None:
    if position_date_iso:
        return position_date_iso[:10]
    for fp in csv_paths:
        ymd8 = ymd8_from_csv_filename(fp.name)
        if ymd8:
            return iso_from_ymd8(ymd8)
    return position_date_iso_from_name(fallback_name)


def _norm_col(name: str) -> str:
    return str(name or "").strip().lstrip("*").strip()


def _pick_col(columns: tuple[str, ...], col_map: dict[str, str]) -> str | None:
    for key in columns:
        nk = _norm_col(key)
        for raw, orig in col_map.items():
            if raw == nk or raw.endswith(nk) or nk in raw:
                return orig
    return None


def parse_alpha_source_csv_bytes(
    data: bytes,
    *,
    date_iso: str,
    source_label: str = "",
) -> list[dict]:
    """解析 CSV：market、ticker、algo_weight、t0_qty、date。"""
    label = source_label or "csv"
    last_err: Exception | None = None
    df = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            df = pd.read_csv(BytesIO(data), encoding=enc, dtype=str)
            break
        except Exception as exc:
            last_err = exc
    if df is None:
        raise RuntimeError(f"无法读取 CSV {label}: {last_err}")

    col_map = {_norm_col(str(c)): c for c in df.columns}
    c_market = _pick_col(("交易市场",), col_map)
    c_ticker = _pick_col(("证券代码",), col_map)
    c_weight = _pick_col(("算法数量/权重",), col_map)
    c_t0 = _pick_col(("T0数量",), col_map)
    missing = [
        n
        for n, c in (
            ("交易市场", c_market),
            ("证券代码", c_ticker),
            ("算法数量/权重", c_weight),
            ("T0数量", c_t0),
        )
        if not c
    ]
    if missing:
        raise ValueError(f"{label} 缺少列 {missing}，当前列: {list(df.columns)}")

    docs: list[dict] = []
    for _, row in df.iterrows():
        ticker_raw = row.get(c_ticker)
        if pd.isna(ticker_raw) or str(ticker_raw).strip() == "":
            continue
        ticker = str(ticker_raw).strip()
        market_raw = row.get(c_market)
        market = "" if pd.isna(market_raw) else str(market_raw).strip()
        weight_raw = row.get(c_weight)
        t0_raw = row.get(c_t0)
        try:
            algo_weight = float(str(weight_raw).replace(",", "").strip()) if not pd.isna(weight_raw) and str(weight_raw).strip() else None
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{label} ticker={ticker!r} 算法数量/权重无法解析: {weight_raw!r}"
            ) from exc
        try:
            t0_qty = float(str(t0_raw).replace(",", "").strip()) if not pd.isna(t0_raw) and str(t0_raw).strip() else None
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{label} ticker={ticker!r} T0数量无法解析: {t0_raw!r}"
            ) from exc
        docs.append(
            {
                "date": date_iso,
                "market": market,
                "ticker": ticker,
                "algo_weight": bson_safe_value(algo_weight),
                "t0_qty": bson_safe_value(t0_qty),
            }
        )
    return docs


def _write_table(
    *,
    date_iso: str,
    table: str,
    docs: list[dict],
    now,
    source_subject: str,
    source_file: str,
) -> int:
    for doc in docs:
        doc["source_subject"] = source_subject
        doc["source_file"] = source_file
        doc["updated_at"] = now
    coll = get_alpha_source_position_collection(table)
    coll.delete_many({"date": date_iso})
    if not docs:
        return 0
    coll.insert_many(docs)
    try:
        coll.create_index(
            [("date", 1), ("ticker", 1)],
            unique=True,
            name=f"uniq_{table}_date_ticker",
        )
    except Exception:
        pass
    return len(docs)


@dataclass
class AlphaSourceMailImportResult:
    status: str
    position_date: str
    target_subject: str = ""
    zip_file: str = ""
    imported: list[dict] = field(default_factory=list)
    files_unmapped: list[str] = field(default_factory=list)
    rows_written: int = 0
    message: str = ""
    error: str = ""


def import_alpha_source_from_csv_paths(
    csv_paths: list[Path],
    *,
    position_date_iso: str,
    source_subject: str,
) -> AlphaSourceMailImportResult:
    """将解压后的 CSV 文件批量写入 position_alpha_source。"""
    now = timezone.now()
    result = AlphaSourceMailImportResult(
        status="FAILED",
        position_date=position_date_iso,
        target_subject=source_subject,
    )
    if not csv_paths:
        result.message = "zip 解压后未找到任何 CSV 文件。"
        return result

    imported: list[dict] = []
    unmapped: list[str] = []
    total_rows = 0
    ymd8_expected = position_date_iso.replace("-", "")

    for fp in sorted(csv_paths):
        table = table_name_for_csv_filename(fp.name)
        if not table:
            unmapped.append(fp.name)
            continue
        file_ymd8 = ymd8_from_csv_filename(fp.name)
        date_iso = (
            iso_from_ymd8(file_ymd8)
            if file_ymd8
            else position_date_iso
        )
        if file_ymd8 and file_ymd8 != ymd8_expected:
            raise ValueError(
                f"CSV {fp.name} 日期前缀 {file_ymd8} 与持仓日 {ymd8_expected} 不一致。"
            )
        docs = parse_alpha_source_csv_bytes(
            fp.read_bytes(),
            date_iso=date_iso,
            source_label=fp.name,
        )
        n = _write_table(
            date_iso=date_iso,
            table=table,
            docs=docs,
            now=now,
            source_subject=source_subject,
            source_file=fp.name,
        )
        total_rows += n
        imported.append({"table": table, "file": fp.name, "rows": n})

    result.imported = imported
    result.files_unmapped = unmapped
    result.rows_written = total_rows

    if not imported:
        result.message = (
            "未导入任何表：zip 内 CSV 均无法映射到已知产品。"
            f" 未映射文件: {', '.join(unmapped) or '无'}"
        )
        return result

    result.status = "SUCCESS"
    miss_note = ""
    if unmapped:
        miss_note = f"；以下 CSV 未映射: {', '.join(unmapped)}"
    result.message = (
        f"已写入 position_alpha_source 共 {len(imported)} 张表、"
        f"{total_rows} 条（date={position_date_iso}）{miss_note}"
    )
    return result


def import_alpha_source_from_zip_file(
    zip_path: Path,
    *,
    position_date_iso: str | None = None,
    extract_dir: Path | None = None,
    keep_extract: bool = False,
) -> AlphaSourceMailImportResult:
    """解压单个 zip 并将内层 CSV 写入 position_alpha_source。"""
    zip_path = Path(zip_path).resolve()
    if not zip_path.is_file():
        return AlphaSourceMailImportResult(
            status="FAILED",
            position_date=position_date_iso or "",
            message=f"zip 不存在: {zip_path}",
        )

    out_dir = extract_dir or (zip_path.parent / "_zip_extract" / zip_path.stem)
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        extract_zip_archive(zip_path, out_dir)
        csv_paths = _csv_paths_in_dir(out_dir)
        pos_iso = _resolve_position_date_iso(
            csv_paths=csv_paths,
            position_date_iso=position_date_iso,
            fallback_name=zip_path.stem,
        )
        if not pos_iso:
            return AlphaSourceMailImportResult(
                status="FAILED",
                position_date="",
                message=f"无法从 zip 或 CSV 解析持仓日: {zip_path.name}",
            )
        result = import_alpha_source_from_csv_paths(
            csv_paths,
            position_date_iso=pos_iso,
            source_subject=build_alpha_source_mail_subject(pos_iso),
        )
        result.zip_file = zip_path.name
        return result
    finally:
        if not keep_extract and out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)


def import_alpha_source_from_zip_dir(
    zip_dir: Path,
    *,
    keep_extract: bool = False,
) -> list[AlphaSourceMailImportResult]:
    """批量导入目录下全部 .zip 文件（按文件名排序）。"""
    root = Path(zip_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"目录不存在: {root}")
    results: list[AlphaSourceMailImportResult] = []
    for zp in sorted(root.glob("*.zip")):
        results.append(
            import_alpha_source_from_zip_file(zp, keep_extract=keep_extract)
        )
    return results
