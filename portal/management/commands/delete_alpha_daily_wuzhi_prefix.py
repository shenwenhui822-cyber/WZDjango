"""
删除 Alpha 产品日报（MongoDB alpha_product.alpha_sim_nav）中，
_schema=alpha_daily 且 product_name 以指定前缀开头的文档。

默认仅统计条数，不删除；须加 --execute 才执行删除。

用法（不加 --execute 仅统计与样例，不删除）：
  python manage.py delete_alpha_daily_wuzhi_prefix
  python manage.py delete_alpha_daily_wuzhi_prefix --execute
  python manage.py delete_alpha_daily_wuzhi_prefix --execute --prefix 吾执
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand

from portal.data.alpha_daily_schema import (
    ALPHA_DAILY_EXCLUDED_PRODUCT_NAME_PREFIX,
    ALPHA_DAILY_SCHEMA,
)
from portal.db.mongo import get_app_collection


def _build_filter(prefix: str) -> dict:
    p = (prefix or "").strip()
    if not p:
        raise ValueError("prefix 不能为空")
    # 前缀匹配（Mongo 正则；不区分大小写可按需改为 re.IGNORECASE）
    escaped = re.escape(p)
    return {
        "_schema": ALPHA_DAILY_SCHEMA,
        "product_name": {"$regex": f"^{escaped}"},
    }


class Command(BaseCommand):
    help = (
        "删除 alpha_sim_nav 中 alpha_daily 且 product_name 以指定前缀开头的文档；"
        "默认 dry-run，加 --execute 才删除"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--prefix",
            default=ALPHA_DAILY_EXCLUDED_PRODUCT_NAME_PREFIX,
            help="产品名称前缀，默认与门户排除前缀一致",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="执行 delete_many；不加则仅统计与抽样展示",
        )
        parser.add_argument(
            "--limit-sample",
            type=int,
            default=20,
            help="dry-run 时最多列出多少条 product_name 样例",
        )

    def handle(self, *args, **options):
        prefix = (options["prefix"] or "").strip()
        execute = bool(options["execute"])
        limit_sample = max(0, int(options["limit_sample"] or 0))

        flt = _build_filter(prefix)
        coll = get_app_collection()

        total = coll.count_documents(flt)
        self.stdout.write(
            f"匹配条件: _schema={ALPHA_DAILY_SCHEMA!r}, "
            f"product_name 以 {prefix!r} 开头，共 {total} 条"
        )

        if total == 0:
            self.stdout.write(self.style.SUCCESS("无匹配文档，无需操作。"))
            return

        if not execute:
            self.stdout.write(self.style.WARNING("未加 --execute，本次不删除（dry-run）。"))
            if limit_sample > 0:
                names = coll.distinct("product_name", flt)
                for i, n in enumerate(sorted(str(x) for x in names if x)[:limit_sample]):
                    self.stdout.write(f"  样例 {i + 1}: {n}")
                if len(names) > limit_sample:
                    self.stdout.write(f"  … 共 {len(names)} 个不同 product_name（仅展示前 {limit_sample} 个排序后名称）")
            self.stdout.write("确认后执行: python manage.py delete_alpha_daily_wuzhi_prefix --execute")
            return

        res = coll.delete_many(flt)
        self.stdout.write(
            self.style.SUCCESS(f"已删除 {res.deleted_count} 条（requested 匹配 {total} 条）。")
        )
