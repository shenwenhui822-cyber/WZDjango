"""
将 Alphadata 目录下所有 .xlsx 导入 MongoDB：alpha_product.alpha_sim_nav

用法：
  python manage.py import_alphadata_xlsx
  python manage.py import_alphadata_xlsx --clear   # 导入前清空集合
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.db.mongo import get_app_collection
from portal.services.import_service import import_excel_fileobj


class Command(BaseCommand):
    help = "导入 Alphadata 下全部 xlsx 到 MongoDB（alpha_product.alpha_sim_nav）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="导入前删除 alpha_sim_nav 集合中的全部文档",
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
            self.stdout.write(self.style.WARNING("已清空目标集合"))

        total_docs = 0
        for path in xlsx_files:
            name = path.name
            try:
                with path.open("rb") as f:
                    stats = import_excel_fileobj(f, name)
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
                            f"已写入 {s['inserted']} 条 [{s['schema']}] <- {name} / {s['sheet']}"
                        )
                    )
            total_docs += int(stats["inserted"])

        self.stdout.write(self.style.SUCCESS(f"完成，累计写入文档数: {total_docs}"))
