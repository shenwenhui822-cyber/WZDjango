"""
将 WZ_ZXDW_MASTER 目录下 Excel 导入 MongoDB 库 fund_nav_real 的四个集合（与博士一号同库）。

目录约定（与博士一号 import_fund_nav_local 类似，按子目录对应集合）::

    <项目根>/WZ_ZXDW_MASTER/
        WZ_ZXDW_MASTER/   *.xls *.xlsx
        WZ_ZXDW_A/
        WZ_ZXDW_B/
        WZ_ZXDW_C/

表头五列：产品名称、产品代码、净值日期、单位净值、累计净值。
同一集合内按 (asset_code, nav_date) upsert。

用法::

  python manage.py import_zxdw_nav_master
  python manage.py import_zxdw_nav_master --dir G:/github/WZDjango/WZ_ZXDW_MASTER
"""
from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.services.zxdw_fund_nav_service import import_zxdw_excel_all_rows


def _list_excel_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    out: list[Path] = []
    for name in os.listdir(directory):
        p = directory / name
        if not p.is_file():
            continue
        lower = name.lower()
        if lower.endswith((".xls", ".xlsx", ".xlsm")):
            out.append(p)
    return sorted(out)


class Command(BaseCommand):
    help = "导入 WZ_ZXDW_MASTER 下四子目录净值 Excel 到 fund_nav_real（WZ_ZXDW_MASTER / WZ_ZXDW_A/B/C）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default="",
            help="根目录，默认 <项目根>/WZ_ZXDW_MASTER（settings.ZXDW_NAV_IMPORT_DIR）",
        )

    def handle(self, *args, **options):
        base = Path(settings.BASE_DIR)
        raw = (options.get("dir") or "").strip()
        root = Path(raw) if raw else Path(getattr(settings, "ZXDW_NAV_IMPORT_DIR", base / "WZ_ZXDW_MASTER"))
        collections = getattr(settings, "MONGODB_ZXDW_NAV_COLLECTIONS", ())

        if not root.is_dir():
            self.stderr.write(self.style.ERROR(f"目录不存在: {root}"))
            return

        total_upsert = 0
        for coll in collections:
            sub = root / coll
            files = _list_excel_files(sub)
            if not files:
                self.stdout.write(self.style.WARNING(f"[{coll}] {sub} 下无 xls/xlsx，跳过"))
                continue
            for fp in files:
                data = fp.read_bytes()
                subj = f"local:{fp.relative_to(root)}"
                try:
                    stat = import_zxdw_excel_all_rows(
                        data,
                        filename=fp.name,
                        collection_name=coll,
                        source_subject=subj,
                    )
                except Exception as exc:
                    self.stderr.write(self.style.ERROR(f"[{coll}] {fp.name} 失败: {exc}"))
                    continue
                errs = stat.get("errors") or []
                if errs:
                    for e in errs[:15]:
                        self.stderr.write(self.style.WARNING(f"{fp.name} {e}"))
                    if len(errs) > 15:
                        self.stderr.write(self.style.WARNING(f"... 另有 {len(errs) - 15} 条错误"))
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[{coll}] {fp.name} 写入 {stat['upserted']} 行，"
                        f"跳过空日期 {stat['skipped_empty_date']} 行"
                    )
                )
                total_upsert += int(stat["upserted"])

        self.stdout.write(self.style.SUCCESS(f"完成，累计 upsert 行数: {total_upsert}"))
