"""
每交易日 08:00 触发：删除上一交易日各账户日度持仓分析 pkl 缓存。

用法：
  python manage.py purge_position_daily_cache
  python manage.py purge_position_daily_cache --force
  python manage.py purge_position_daily_cache --trade-date 2026-06-18
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.services.trade_calendar_service import (
    is_trade_date_iso,
    prev_trading_day_iso_before,
)
from position_daily.services.cache import CACHE_DIR, purge_cached_reports


class Command(BaseCommand):
    help = (
        "仅运行日为交易日时执行（定时 08:00）：删除上一交易日 "
        "reports/cache/<strategy_tag>/<YYYY-MM-DD>.pkl 日度持仓分析缓存。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="忽略「运行日须为交易日」校验。",
        )
        parser.add_argument(
            "--trade-date",
            default="",
            help="要删除的快照日 YYYY-MM-DD；默认删除运行日的上一交易日缓存。",
        )

    def handle(self, *args, **options):
        run_iso = timezone.localdate().isoformat()
        run_is_trading = is_trade_date_iso(run_iso)
        self.stdout.write(f"运行日: {run_iso}，是否交易日: {run_is_trading}")

        if not options["force"] and not run_is_trading:
            self.stdout.write("非交易日，跳过 purge_position_daily_cache。")
            return

        trade_date = (options["trade_date"] or "").strip()
        if trade_date:
            purge_day = trade_date
        else:
            purge_day = prev_trading_day_iso_before(run_iso) or ""
            if not purge_day:
                self.stderr.write("无法解析上一交易日，未删除任何缓存。")
                return

        self.stdout.write(f"目标快照日（上一交易日）: {purge_day}")
        self.stdout.write(f"缓存目录: {CACHE_DIR}")

        result = purge_cached_reports(purge_day)
        removed = result.get("removed") or []
        errors = result.get("errors") or []
        count = int(result.get("removed_count") or 0)

        self.stdout.write(f"已删除 pkl 文件: {count} 个")
        for path in removed:
            self.stdout.write(f"  - {path}")
        if not removed:
            self.stdout.write("  （无匹配文件，可能此前已清理或未生成缓存）")
        for err in errors:
            self.stderr.write(f"删除失败: {err}")

        if errors:
            self.stderr.write(
                self.style.ERROR(
                    f"purge_position_daily_cache 部分失败: {len(errors)} 个文件"
                )
            )
            raise SystemExit(1)

        self.stdout.write(
            self.style.SUCCESS(
                f"purge_position_daily_cache 完成: {purge_day} 共 {count} 个"
            )
        )
