#!/usr/bin/env python

import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

CSV_FILES = "sample_volatility.csv"
MONGODB_URI = "mongodb://option:volatility@192.168.110.199:27017/?authSource=admin"

ETF_COLUMNS = (
    "ETF_510050",
    "ETF_510300",
    "ETF_510500",
    "ETF_588000",
    "ETF_588080",
    "ETF_159901",
    "ETF_159915",
    "ETF_159919",
    "ETF_159922",
)

_DIR = Path(__file__).resolve().parent

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except ImportError:
    print("请先安装 pymongo: pip install pymongo", file=sys.stderr)
    sys.exit(1)


def _csv_path() -> Path:
    name = (CSV_FILES or "").strip()
    if not name or name != Path(name).name or not name.lower().endswith(".csv"):
        sys.exit("CSV_FILES 须为同级目录下的 .csv 文件名。")
    path = (_DIR / name).resolve()
    if path.parent != _DIR or not path.is_file():
        sys.exit(f"找不到文件: {path}")
    return path


def _parse_date(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date().isoformat()
    if isinstance(val, date):
        return val.isoformat()
    s = str(val).strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    d = re.sub(r"[^\d]", "", s)[:8]
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None


def _parse_vol(val) -> float | None:
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if not s:
        return None
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _read_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            sys.exit("CSV 无表头。")
        cols = {c.strip().lstrip("\ufeff").lower(): c for c in reader.fieldnames}
        date_k = cols.get("date") or cols.get("日期")
        if not date_k:
            sys.exit("CSV 须含 date 列（或 日期）。")
        etf_keys: dict[str, str | None] = {}
        missing = []
        for col in ETF_COLUMNS:
            k = cols.get(col.lower())
            if not k:
                missing.append(col)
            etf_keys[col] = k
        if missing:
            sys.exit(f"CSV 缺少 ETF 列: {', '.join(missing)}")
        for row in reader:
            day = _parse_date(row.get(date_k))
            if not day:
                continue
            doc: dict = {"date": day}
            has_any = False
            for field, csv_k in etf_keys.items():
                v = _parse_vol(row.get(csv_k)) if csv_k else None
                doc[field] = v
                if v is not None:
                    has_any = True
            if has_any:
                rows.append(doc)
    if not rows:
        sys.exit("无有效数据行。")
    return rows


def main() -> None:
    path = _csv_path()
    rows = _read_rows(path)

    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=15_000)
    try:
        client.admin.command("ping")
    except PyMongoError as e:
        sys.exit(f"MongoDB 连接失败: {e}")

    coll = client["option"]["volatility"]
    coll.create_index([("date", 1)], unique=True, name="ux_date")

    new, upd = 0, 0
    for doc in rows:
        r = coll.update_one({"date": doc["date"]}, {"$set": doc}, upsert=True)
        if r.upserted_id:
            new += 1
        elif r.modified_count:
            upd += 1

    print(
        f"完成: {path.name} → option.volatility，"
        f"{len(rows)} 条（date + {len(ETF_COLUMNS)} ETF），新增 {new}，更新 {upd}"
    )


if __name__ == "__main__":
    main()
