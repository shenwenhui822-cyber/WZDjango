"""
将 WZ_ZXDW_MASTER 目录下 Excel 导入 MongoDB 库 fund_nav_real 的四个集合（与博士一号同库）。

目录约定（与博士一号 import_fund_nav_local 类似，按子目录对应集合）::

    <项目根>/WZ_ZXDW_MASTER/
        WZ_ZXDW_MASTER/   *.xls *.xlsx
        WZ_ZXDW_A/
        WZ_ZXDW_B/
        WZ_ZXDW_C/

表头：产品名称、产品代码、净值日期、单位净值、累计净值或累计单位净值（写入 cumulative_unit_nav）；可选列 基金资产净值、基金资产份额。
同一集合内按 (asset_code, nav_date) upsert。可选 --only-stz051：仅导入产品代码 STZ051（适合横表多列）。

用法::

  python manage.py import_zxdw_nav_master
  python manage.py import_zxdw_nav_master --dir G:/github/WZDjango/WZ_ZXDW_MASTER
  python manage.py import_zxdw_nav_master --file "吾执泽鑫多维私募证券投资基金_净值序列_实盘起始日2026-2-9.xlsx"
  python manage.py import_zxdw_nav_master --file xxx.xlsx --only-stz051
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
    help = (
        "导入泽鑫多维净值 Excel：支持目录批量（四子目录）或单文件 --file（写入 WZ_ZXDW_MASTER）"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default="",
            help="根目录，默认 <项目根>/WZ_ZXDW_MASTER（settings.ZXDW_NAV_IMPORT_DIR）；与 --file 二选一",
        )
        parser.add_argument(
            "--file",
            "-f",
            default="",
            help="单个 xlsx/xls 路径（相对项目根或绝对路径）；写入 fund_nav_real.WZ_ZXDW_MASTER",
        )
        parser.add_argument(
            "--only-stz051",
            action="store_true",
            help="仅导入产品代码为 STZ051 的行（跳过 TZ051A/TZ051B 等；横表/竖表均生效）",
        )

    def handle(self, *args, **options):
        base = Path(settings.BASE_DIR)
        file_raw = (options.get("file") or "").strip()
        if file_raw:
            self._import_single_file(base, options)
            return

        raw = (options.get("dir") or "").strip()
        root = Path(raw) if raw else Path(getattr(settings, "ZXDW_NAV_IMPORT_DIR", base / "WZ_ZXDW_MASTER"))
        collections = getattr(settings, "MONGODB_ZXDW_NAV_COLLECTIONS", ())

        if not root.is_dir():
            self.stderr.write(self.style.ERROR(f"目录不存在: {root}"))
            return

        only_codes = frozenset({"STZ051"}) if options.get("only_stz051") else None

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
                        only_asset_codes=only_codes,
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

    def _import_single_file(self, base: Path, options: dict) -> None:
        raw = (options.get("file") or "").strip()
        path = Path(raw)
        if not path.is_file():
            alt = base / raw
            if alt.is_file():
                path = alt
            else:
                self.stderr.write(self.style.ERROR(f"文件不存在: {raw}"))
                return

        lower = path.name.lower()
        if not lower.endswith((".xls", ".xlsx", ".xlsm")):
            self.stderr.write(self.style.ERROR("仅支持 .xls / .xlsx / .xlsm"))
            return

        allowed = getattr(settings, "MONGODB_ZXDW_NAV_COLLECTIONS", ())
        target_coll = "WZ_ZXDW_MASTER"
        if target_coll not in allowed:
            target_coll = allowed[0] if allowed else "WZ_ZXDW_MASTER"

        only_codes = frozenset({"STZ051"}) if options.get("only_stz051") else None
        data = path.read_bytes()
        subj = f"local:{path.name}"
        try:
            stat = import_zxdw_excel_all_rows(
                data,
                filename=path.name,
                collection_name=target_coll,
                source_subject=subj,
                only_asset_codes=only_codes,
            )
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"导入失败: {exc}"))
            raise

        errs = stat.get("errors") or []
        for e in errs[:15]:
            self.stderr.write(self.style.WARNING(e))
        if len(errs) > 15:
            self.stderr.write(self.style.WARNING(f"... 另有 {len(errs) - 15} 条错误"))

        self.stdout.write(
            self.style.SUCCESS(
                f"[{target_coll}] {path.name} 写入 {stat['upserted']} 行，"
                f"跳过空日期 {stat['skipped_empty_date']} 行"
            )
        )
