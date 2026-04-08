"""
邮箱 IMAP 配置：从环境变量读取（推荐在项目根目录 `.env` 中配置，由 settings 加载）。
"""
from __future__ import annotations

import os


def resolve_imap_credentials() -> tuple[str, str, str, int]:
    user = (os.getenv("ALPHA_MAIL_USER") or "").strip()
    pwd = (os.getenv("ALPHA_MAIL_PASS") or "").strip()
    host = (os.getenv("ALPHA_IMAP_SERVER") or "imap.exmail.qq.com").strip()
    port = int(os.getenv("ALPHA_IMAP_PORT") or "993")
    if user and pwd:
        return user, pwd, host, port

    raise RuntimeError(
        "未配置邮箱：请在项目根目录 `.env` 中设置 ALPHA_MAIL_USER、ALPHA_MAIL_PASS，"
        "或设置同名环境变量。"
    )
