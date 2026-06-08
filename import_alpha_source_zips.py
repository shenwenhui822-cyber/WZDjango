#!/usr/bin/env python3
"""
批量导入 zip 目录内吾执持仓 CSV 到 MongoDB position_alpha_source。

默认目录：项目根 date/zip

用法：
  python import_alpha_source_zips.py
  python import_alpha_source_zips.py --zip-dir G:\\github\\WZDjango\\date\\zip
  python import_alpha_source_zips.py --keep-extract
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="批量导入 zip → position_alpha_source"
    )
    parser.add_argument(
        "--zip-dir",
        default="",
        help="zip 目录；默认 <项目根>/date/zip",
    )
    parser.add_argument(
        "--keep-extract",
        action="store_true",
        help="保留解压目录 date/zip/_zip_extract/",
    )
    args = parser.parse_args()

    root = _project_root()
    zip_dir = Path(args.zip_dir).expanduser().resolve() if args.zip_dir.strip() else root / "date" / "zip"

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wzproject.settings")
    import django

    django.setup()

    from portal.services.alpha_source_position_mail_service import (
        import_alpha_source_from_zip_dir,
    )

    if not zip_dir.is_dir():
        print(f"错误: 目录不存在 {zip_dir}", file=sys.stderr)
        return 1

    zips = sorted(zip_dir.glob("*.zip"))
    if not zips:
        print(f"目录内无 zip 文件: {zip_dir}", file=sys.stderr)
        return 1

    print(f"zip 目录: {zip_dir}")
    print(f"待导入: {len(zips)} 个 zip\n")

    results = import_alpha_source_from_zip_dir(
        zip_dir, keep_extract=bool(args.keep_extract)
    )

    ok = 0
    fail = 0
    for zp, res in zip(zips, results):
        if res.status == "SUCCESS":
            ok += 1
            print(f"[OK]   {zp.name}  date={res.position_date}  rows={res.rows_written}")
            for row in res.imported:
                print(f"       - {row['table']}: {row['rows']} 条")
        else:
            fail += 1
            print(f"[FAIL] {zp.name}  {res.message}", file=sys.stderr)

    print(f"\n完成: 成功 {ok}，失败 {fail}，共 {len(results)} 个 zip")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
