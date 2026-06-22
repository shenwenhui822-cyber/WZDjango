from django.apps import AppConfig


class PositionDailyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "position_daily"
    verbose_name = "日度持仓分析（后端）"
