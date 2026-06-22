from pathlib import Path

from django.core.management.base import BaseCommand

from position_daily.services.config import STRATEGY_TAG
from position_daily.services.position import get_latest_snapshot_date
from position_daily.services.report_builder import build_daily_report, render_markdown


class Command(BaseCommand):
    help = "生成日度持仓 Markdown 报告"

    def add_arguments(self, parser):
        parser.add_argument(
            "trade_date",
            nargs="?",
            default="latest",
            help="快照日期 YYYY-MM-DD，或 latest",
        )
        parser.add_argument(
            "--strategy",
            default=STRATEGY_TAG,
            help=f"strategy_tag / 集合名，默认 {STRATEGY_TAG}",
        )
        parser.add_argument(
            "--output-dir",
            default="position_daily/reports/daily",
            help="输出目录，默认 position_daily/reports/daily/{strategy}/{date}.md",
        )

    def handle(self, *args, **options):
        strategy_tag = (options["strategy"] or STRATEGY_TAG).strip()
        trade_date = options["trade_date"]
        if trade_date == "latest":
            trade_date = get_latest_snapshot_date(strategy_tag=strategy_tag)
            if not trade_date:
                self.stderr.write("无可用持仓快照")
                return

        ctx = build_daily_report(trade_date, strategy_tag=strategy_tag)
        if ctx.error:
            self.stderr.write(ctx.error)
            return

        md = render_markdown(ctx)
        out_dir = Path(options["output_dir"]) / strategy_tag
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{trade_date}.md"
        out_path.write_text(md, encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"written: {out_path}"))
