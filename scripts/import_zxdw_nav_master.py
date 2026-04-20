#!/usr/bin/env python
"""独立入口：将 WZ_ZXDW_MASTER 下 Excel 导入 fund_nav_real（等价于 manage.py import_zxdw_nav_master）。"""
from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, repo)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wzproject.settings")
    import django

    django.setup()
    from django.core.management import call_command

    call_command("import_zxdw_nav_master", *sys.argv[1:])
