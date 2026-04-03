"""
将 Alphadata 目录下所有 .xlsx 导入 MongoDB：testdb.appdb

用法：
  python manage.py import_alphadata_xlsx
  python manage.py import_alphadata_xlsx --clear   # 导入前清空集合
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand

from portal.data.alpha_daily_schema import is_alpha_daily_sheet, sheet_df_to_alpha_daily_records
from portal.data import value_parsers as vp
from portal.mongo_utils import bson_safe_value, get_app_collection


def _report_date_from_xlsx_filename(filename: str) -> str | None:
    """从文件名中的 YYYYMMDD 解析报表日期（如 Alpha产品表现汇总_20220921.xlsx）。"""
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", filename)
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    return f"{y}-{mo}-{d}"


def _inject_report_date_alpha_daily(
    records: list[dict], source_filename: str
) -> None:
    day = _report_date_from_xlsx_filename(source_filename)
    if not day:
        return
    parsed = vp.parse_report_date(day)
    if parsed is None:
        return
    for rec in records:
        if rec.get("report_date") is None:
            rec["report_date"] = parsed


def _sheet_to_records_legacy(
    df: pd.DataFrame, source_file: str, sheet_name: str
) -> list[dict]:
    df = df.copy()
    # 列名统一为字符串，避免 int 列名
    df.columns = [str(c) for c in df.columns]
    records: list[dict] = []
    for idx, row in df.iterrows():
        item: dict = {
            "_source_file": source_file,
            "_sheet_name": sheet_name,
            "_row_index": int(idx),
        }
        for col in df.columns:
            item[col] = bson_safe_value(row[col])
        records.append(item)
    return records


def _sheet_to_records_auto(
    df, source_file: str, sheet_name: str
) -> tuple[list[dict], str]:
    """Alpha 日报（英文字段 + 类型解析）或旧版原样导入。返回 (records, schema_tag)。"""
    if is_alpha_daily_sheet(df):
        batch = sheet_df_to_alpha_daily_records(df, source_file, sheet_name)
        _inject_report_date_alpha_daily(batch, source_file)
        return batch, "alpha_daily"
    return _sheet_to_records_legacy(df, source_file, sheet_name), "raw"


class Command(BaseCommand):
    help = "导入 Alphadata 下全部 xlsx 到 MongoDB（testdb.appdb）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="导入前删除 appdb 集合中的全部文档",
        )

    def handle(self, *args, **options):
        data_dir: Path = settings.ALPHADATA_DIR
        if not data_dir.is_dir():
            self.stdout.write(self.style.WARNING(f"目录不存在，已创建: {data_dir}"))
            data_dir.mkdir(parents=True, exist_ok=True)

        xlsx_files = sorted(data_dir.glob("*.xlsx"))
        if not xlsx_files:
            self.stdout.write(
                self.style.WARNING(
                    f"未找到 xlsx 文件，请将文件放入: {data_dir}"
                )
            )
            return

        coll = get_app_collection()
        if options["clear"]:
            coll.delete_many({})
            self.stdout.write(self.style.WARNING("已清空集合 appdb"))

        total_docs = 0
        for path in xlsx_files:
            name = path.name
            try:
                xl = pd.ExcelFile(path, engine="openpyxl")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"跳过（无法打开）{name}: {e}"))
                continue

            for sheet_name in xl.sheet_names:
                try:
                    df = xl.parse(sheet_name)
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"跳过 sheet {name}::{sheet_name}: {e}")
                    )
                    continue
                if df.empty:
                    self.stdout.write(
                        self.style.NOTICE(f"跳过空表: {name}::{sheet_name}")
                    )
                    continue
                batch, schema_tag = _sheet_to_records_auto(df, name, sheet_name)
                if not batch:
                    continue
                try:
                    coll.insert_many(batch, ordered=False)
                    n = len(batch)
                    total_docs += n
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"已写入 {n} 条 [{schema_tag}] <- {name} / {sheet_name}"
                        )
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"写入失败 {name}::{sheet_name}: {e}")
                    )

        self.stdout.write(self.style.SUCCESS(f"完成，累计写入文档数: {total_docs}"))
