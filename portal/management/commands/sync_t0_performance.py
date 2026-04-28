"""从 FTP daily_report 同步 *_wuzhi_日内交易汇总.xlsx 到 T0_performance.daily_report。"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from portal.t0_performance.sync_service import sync_t0_from_ftp


class Command(BaseCommand):
    help = "FTP 拉取 wuzhi 日内交易汇总并入库（见 settings MONGODB_T0_PERFORMANCE_*）"

    def handle(self, *args, **options):
        r = sync_t0_from_ftp()
        if r.errors:
            for e in r.errors:
                self.stdout.write(self.style.WARNING(e))
        self.stdout.write(
            self.style.SUCCESS(
                f"完成：处理 {r.files_processed} 个文件，upsert {r.rows_upserted} 行。"
            )
        )
