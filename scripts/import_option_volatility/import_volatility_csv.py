#!/usr/bin/env python

import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

CSV_FILES = "sample_volatility.csv"
MONGODB_URI = "mongodb://option:volatility@192.168.110.199:27017/?authSource=admin"

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
        vol_k = cols.get("volatility_num") or cols.get("波动率")
        if not date_k or not vol_k:
            sys.exit("CSV 须含 date、volatility_num 列（或 日期、波动率）。")
        for row in reader:
            day, vol = _parse_date(row.get(date_k)), _parse_vol(row.get(vol_k))
            if day and vol is not None:
                rows.append({"date": day, "volatility_num": vol})
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

    print(f"完成: {path.name} → option.volatility，{len(rows)} 条，新增 {new}，更新 {upd}")


if __name__ == "__main__":
    main()
