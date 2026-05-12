#!/usr/bin/env python
"""独立入口：吾执多元 CTA 一号净值序列 Excel → fund_nav_real.WZ_DYCTAYH_MASTER（等价 manage.py import_dyctayh_nav_series）。"""
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

    call_command("import_dyctayh_nav_series", *sys.argv[1:])
