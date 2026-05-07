"""
将吾执二二号 STZ053「净值序列」Excel（多行历史表）导入 MongoDB：fund_nav_real.WZ_EEH_MASTER。

表头（首行）须包含：产品名称、产品代码、净值日期、单位净值、累计净值；可选 基金资产净值。
列名与 portal.services.fund_nav_real_service._HEADER_KEYS 中别名一致，落库字段与
auto_import_stz053_nav_mail + upsert_fund_nav_doc 相同（nav_date 唯一 upsert）。

示例文件：吾执二二号私募证券投资基金_净值序列_20260423.xlsx

用法：
  python manage.py import_stz053_nav_series --file 吾执二二号私募证券投资基金_净值序列_20260423.xlsx
  python manage.py import_stz053_nav_series --file G:/path/to/file.xlsx
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.services.fund_nav_real_service import import_fund_nav_excel_all_rows
from portal.services.stz053_daily_nav_service import get_stz053_fund_product


class Command(BaseCommand):
    help = (
        "导入吾执二二号 STZ053 净值序列 Excel（多行）到 fund_nav_real，"
        "集合名为 settings.NAV_REAL_WZ_EEH_MASTER（默认 WZ_EEH_MASTER）。"
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

        fund = get_stz053_fund_product()
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
