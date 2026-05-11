"""
将吾执多元量选 SAJM64「净值序列」Excel（多行）导入 MongoDB：fund_nav_real.WZ_DYLX_MASTER。

表头须含：日期、资产代码、资产名称、资产份额净值、资产份额累计净值等（同 portal.services.fund_nav_real_service._HEADER_KEYS）。
落库与 auto_import_dylx_nav_mail 相同（nav_date 唯一 upsert）。

用法：
  python manage.py import_dylx_nav_series --file "xxx.xlsx"
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.services.dylx_nav_mail_service import get_dylx_fund_product
from portal.services.fund_nav_real_service import import_fund_nav_excel_all_rows


class Command(BaseCommand):
    help = (
        "导入吾执多元量选 SAJM64 净值序列 Excel 到 fund_nav_real，"
        "集合为 settings.NAV_REAL_WZ_DYLX_MASTER（默认 WZ_DYLX_MASTER）。"
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

        fund = get_dylx_fund_product()
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
