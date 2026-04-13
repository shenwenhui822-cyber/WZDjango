"""
将本地目录中的博士一号净值 Excel（多行历史表）导入 MongoDB：fund_nav_real.WZ_BSYH_MASTER / fund_nav_real.WZ_BSYH_B。

默认目录：项目根下 downloaded_attachments_boshiyihao
同一集合（按产品分库）内 nav_date 唯一，重复导入为覆盖更新。

用法：
  python manage.py import_fund_nav_local
  python manage.py import_fund_nav_local --dir G:/github/WZDjango/downloaded_attachments_boshiyihao
"""
from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.data.fund_nav_real_config import fund_from_filename
from portal.services.fund_nav_real_service import import_fund_nav_excel_all_rows


def _list_target_files(directory: Path) -> list[Path]:
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
    help = "导入本地下载目录中的博士一号净值表（多行）到 fund_nav_real 下各产品集合"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default="",
            help="附件目录，默认 <项目根>/downloaded_attachments_boshiyihao",
        )

    def handle(self, *args, **options):
        base = Path(settings.BASE_DIR)
        raw = (options.get("dir") or "").strip()
        target_dir = Path(raw) if raw else base / "downloaded_attachments_boshiyihao"
        if not target_dir.is_dir():
            self.stderr.write(self.style.ERROR(f"目录不存在: {target_dir}"))
            return

        files = _list_target_files(target_dir)
        if not files:
            self.stdout.write(self.style.WARNING(f"{target_dir} 下无 xls/xlsx 文件"))
            return

        total_upsert = 0
        for fp in files:
            fund = fund_from_filename(fp.name)
            if fund is None:
                self.stdout.write(self.style.WARNING(f"跳过（无法从文件名识别产品）: {fp.name}"))
                continue
            data = fp.read_bytes()
            subj = f"local:{fp.name}"
            stat = import_fund_nav_excel_all_rows(
                data,
                filename=fp.name,
                fund=fund,
                source_subject=subj,
            )
            errs = stat.get("errors") or []
            if errs:
                for e in errs[:20]:
                    self.stderr.write(self.style.WARNING(f"{fp.name} {e}"))
                if len(errs) > 20:
                    self.stderr.write(self.style.WARNING(f"... 另有 {len(errs) - 20} 条错误"))
            self.stdout.write(
                self.style.SUCCESS(
                    f"{fp.name} [{fund['product_key']}] 写入 {stat['upserted']} 行，"
                    f"跳过空日期 {stat['skipped_empty_date']} 行"
                )
            )
            total_upsert += int(stat["upserted"])

        self.stdout.write(self.style.SUCCESS(f"完成，累计 upsert 行数: {total_upsert}"))
