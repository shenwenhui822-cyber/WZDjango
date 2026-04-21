from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "兼容入口：等价于 auto_import_cjqh_settle_mail"

    def add_arguments(self, parser):
        parser.add_argument(
            "--subject-date",
            default="",
            help="主题日期，格式 YYYYMMDD 或 YYYY-MM-DD；默认今天（Asia/Shanghai）。",
        )
        parser.add_argument(
            "--days",
            default=5,
            type=int,
            help="IMAP 检索范围（近 N 天，默认 5）。",
        )
        parser.add_argument(
            "--account-id",
            default="81801575",
            help="账号（默认 81801575）。",
        )

    def handle(self, *args, **options):
        call_command(
            "auto_import_cjqh_settle_mail",
            subject_date=options.get("subject_date") or "",
            days=int(options.get("days") or 5),
            account_id=options.get("account_id") or "81801575",
        )
