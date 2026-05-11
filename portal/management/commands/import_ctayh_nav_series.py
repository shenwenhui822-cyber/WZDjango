"""
将吾执 CTA 一号 SNG191「净值序列」Excel 导入 MongoDB：fund_nav_real.WZ_CTAYH_MASTER。

支持图二版式：表头含 产品代码、产品名称、估值日期、单位净值、累计单位净值、资产净值 等；
多行按日 upsert。亦兼容图一单日单行。

用法：
  python manage.py import_ctayh_nav_series --file "产品净值信息.xlsx"
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.services.ctayh_nav_mail_service import get_ctayh_fund_product
from portal.services.fund_nav_real_service import import_fund_nav_excel_all_rows


class Command(BaseCommand):
    help = (
        "导入吾执 CTA 一号 SNG191 净值序列 Excel 到 fund_nav_real，"
        "集合为 settings.NAV_REAL_WZ_CTAYH_MASTER（默认 WZ_CTAYH_MASTER）。"
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

        fund = get_ctayh_fund_product()
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
