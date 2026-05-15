"""
将吾执零零号 SNP584「净值序列」Excel（多行）导入 MongoDB：
fund_nav_real.WZ_LLH_MASTER（主基金）与 WZ_LLH_A（A 类）。

表头须含：净值日期或日期、产品代码、产品名称、单位净值、累计单位净值等；
含「分级名称」列时 A 类按分级名称匹配。落库与 auto_import_llh_nav_mail 相同（nav_date 唯一 upsert）。

用法：
  python manage.py import_llh_nav_series --file "xxx.xlsx"
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.services.fund_nav_real_service import import_fund_nav_excel_all_rows
from portal.services.llh_nav_mail_service import llh_nav_mail_import_products


class Command(BaseCommand):
    help = (
        "导入吾执零零号 SNP584 净值序列 Excel 到 fund_nav_real，"
        "集合为 WZ_LLH_MASTER（主基金）与 WZ_LLH_A（A 类）。"
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

        data = path.read_bytes()
        source_subject = f"local:{path.name}"
        total_upserted = 0
        total_skipped = 0
        all_errors: list[str] = []

        for fund in llh_nav_mail_import_products():
            try:
                stat = import_fund_nav_excel_all_rows(
                    data,
                    filename=path.name,
                    fund=fund,
                    source_subject=source_subject,
                )
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"[{fund['product_key']}] 导入失败: {exc}")
                )
                raise

            errs = stat.get("errors") or []
            all_errors.extend(f"[{fund['product_key']}] {e}" for e in errs)
            total_upserted += int(stat.get("upserted") or 0)
            total_skipped += int(stat.get("skipped_empty_date") or 0)
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{fund['product_key']}] {path.name} 写入 {stat['upserted']} 行，"
                    f"跳过空日期 {stat['skipped_empty_date']} 行"
                )
            )

        for e in all_errors[:30]:
            self.stderr.write(self.style.WARNING(e))
        if len(all_errors) > 30:
            self.stderr.write(self.style.WARNING(f"... 另有 {len(all_errors) - 30} 条错误"))

        self.stdout.write(
            self.style.SUCCESS(
                f"合计写入 {total_upserted} 行，跳过空日期 {total_skipped} 行"
            )
        )
