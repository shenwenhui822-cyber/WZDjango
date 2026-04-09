"""将 trade_dates_all/trade_dates_all.csv 导入 MongoDB alpha_product.trade_calendar。"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.services.trade_calendar_service import import_trade_dates_csv


class Command(BaseCommand):
    help = "导入交易日 CSV 到 MongoDB（alpha_product.trade_calendar）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="导入前清空 trade_calendar 集合",
        )
        parser.add_argument(
            "--path",
            type=str,
            default="",
            help=f"CSV 路径，默认 {settings.TRADE_DATES_CSV}",
        )

    def handle(self, *args, **options):
        p = (options["path"] or "").strip()
        csv_path = Path(p) if p else settings.TRADE_DATES_CSV
        try:
            stats = import_trade_dates_csv(csv_path, clear=options["clear"])
        except Exception as e:
            self.stdout.write(self.style.ERROR(str(e)))
            return
        self.stdout.write(self.style.SUCCESS(str(stats)))
