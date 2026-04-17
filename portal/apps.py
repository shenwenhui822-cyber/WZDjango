from django.apps import AppConfig
import os
import sys


class PortalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "portal"
    verbose_name = "吾执内部投研平台"

    def ready(self):
        # 开发/部署启动 Django 进程后自动进入定时循环（可用环境变量关闭）
        if os.getenv("ALPHA_SCHEDULER_ENABLED", "1").strip() in ("0", "false", "False"):
            return
        # 避免 runserver 自动重载导致启动两次
        if "runserver" in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return
        # 仅在 runserver 场景自动启动，避免 migrate/check 等命令被常驻线程污染
        if "runserver" not in sys.argv:
            return
        from portal.scheduler.alpha_mail_scheduler import start_scheduler_background

        start_scheduler_background()
