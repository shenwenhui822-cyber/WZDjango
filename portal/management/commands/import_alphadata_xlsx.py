"""
将 Alphadata 目录下所有 .xlsx 导入 MongoDB：alpha_product.alpha_sim_nav

产品名规范化与 auto_import_alpha_mail 一致：
  - 新Alpha产品表现汇总_YYYYMMDD.xlsx：所有产品名加「产品-」前缀
  - 其他（含 Alpha产品表现汇总_YYYYMMDD.xlsx）：仅映射 alpha_daily_schema 中指定简称

用法：
  python manage.py import_alphadata_xlsx
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.data.alpha_daily_schema import (
    is_alpha_daily_new_summary_xlsx_filename,
    resolve_alpha_daily_product_name_normalizer_for_filename,
)
from portal.services.import_service import import_excel_fileobj


class Command(BaseCommand):
    help = "导入 Alphadata 下全部 xlsx 到 MongoDB（alpha_product.alpha_sim_nav）"

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

        total_docs = 0
        for path in xlsx_files:
            name = path.name
            import_mode = (
                "new_summary"
                if is_alpha_daily_new_summary_xlsx_filename(name)
                else "legacy"
            )
            name_normalizer = resolve_alpha_daily_product_name_normalizer_for_filename(
                name
            )
            try:
                with path.open("rb") as f:
                    stats = import_excel_fileobj(
                        f,
                        name,
                        alpha_daily_product_name_normalizer=name_normalizer,
                    )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"跳过（无法打开）{name}: {e}"))
                continue

            for s in stats["sheets"]:
                if s.get("skipped"):
                    self.stdout.write(
                        self.style.NOTICE(
                            f"跳过 {name} / {s['sheet']} [{s['schema']}]"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"已写入 {s['inserted']} 条 [{s['schema']},{import_mode}] "
                            f"<- {name} / {s['sheet']}"
                        )
                    )
            total_docs += int(stats["inserted"])

        self.stdout.write(self.style.SUCCESS(f"完成，累计写入文档数: {total_docs}"))
