"""
将吾执九零号 SXR194「净值序列」Excel 导入 MongoDB：fund_nav_real.WZ_JLH_MASTER。

支持两种常见版式：
  - 首行即表头（净值日期、产品代码、…）
  - 首行为标题「产品基金净值数据」、第二行为表头（图二模板）

仅落库产品代码为 SXR194 的行。

用法：
  python manage.py import_jlh_nav_series --file "九零号净值.xlsx"
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.services.fund_nav_real_service import import_fund_nav_excel_all_rows
from portal.services.jlh_nav_mail_service import get_jlh_fund_product


class Command(BaseCommand):
    help = (
        "导入吾执九零号 SXR194 净值序列 Excel 到 fund_nav_real，"
        "集合为 settings.NAV_REAL_WZ_JLH_MASTER（默认 WZ_JLH_MASTER）；忽略非 SXR194 行。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            "-f",
            required=True,
            help="净值序列 xlsx/xls 路径（相对项目根或绝对路径）",
        )

    def handle(self, *args, **options):
        raw = (options.get("file") or "").strip()
        if not raw:
            self.stderr.write(self.style.ERROR("请使用 --file 指定 Excel 路径"))
            return

        base = Path(settings.BASE_DIR)
        path = Path(raw)
        if not path.is_file():
            alt = base / raw
            if alt.is_file():
                path = alt
            else:
                self.stderr.write(self.style.ERROR(f"文件不存在: {raw}"))
                return

        fund = get_jlh_fund_product()
        data = path.read_bytes()
        source_subject = f"local:{path.name}"

        try:
            stat = import_fund_nav_excel_all_rows(
                data,
                filename=path.name,
                fund=fund,
                source_subject=source_subject,
            )
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"导入失败: {exc}"))
            raise

        errs = stat.get("errors") or []
        for e in errs[:30]:
            self.stderr.write(self.style.WARNING(e))
        if len(errs) > 30:
            self.stderr.write(self.style.WARNING(f"... 另有 {len(errs) - 30} 条错误"))

        self.stdout.write(
            self.style.SUCCESS(
                f"[{fund['product_key']}] {path.name} 写入 {stat['upserted']} 行，"
                f"跳过空日期 {stat['skipped_empty_date']} 行"
            )
        )
