"""将 qichat 目录下 CSV 导入 T0_performance.t0_order。"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.t0_performance.qichat_import import import_qichat_csv_dir


class Command(BaseCommand):
    help = "导入 qichat/*.csv 到 MongoDB（见 MONGODB_T0_ORDER_COLLECTION）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            type=str,
            default="",
            help=f"CSV 目录，默认 {settings.T0_QICHAT_IMPORT_DIR}",
        )

    def handle(self, *args, **options):
        d = (options.get("dir") or "").strip()
        from pathlib import Path

        path = Path(d) if d else settings.T0_QICHAT_IMPORT_DIR
        r = import_qichat_csv_dir(path)
        for e in r.errors:
            self.stdout.write(self.style.WARNING(e))
        self.stdout.write(
            self.style.SUCCESS(
                f"完成：{r.files_processed} 个文件，upsert {r.rows_upserted} 行。"
            )
        )
