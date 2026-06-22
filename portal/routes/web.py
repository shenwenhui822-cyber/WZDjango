from django.urls import path

from portal.t0_performance import views as t0_views
from portal.views import (
    account_brief_views,
    alpha_daily_views,
    auth_views,
    mail_logs_views,
    nav_curve_views,
    option_metrics_views,
    position_daily_views,
)

web_urlpatterns = [
    path("", auth_views.index, name="index"),
    path("home/", auth_views.home, name="home"),
    path("alpha/daily/", alpha_daily_views.alpha_daily, name="alpha_daily"),
    path(
        "alpha/daily/import/",
        alpha_daily_views.alpha_daily_import,
        name="alpha_daily_import",
    ),
    path("nav/curve/", nav_curve_views.nav_curve, name="nav_curve"),
    path("nav/bench-compare/", nav_curve_views.nav_bench_compare, name="nav_bench_compare"),
    path("alpha/t0/", t0_views.t0_performance, name="alpha_t0"),
    # 前端暂不展示：对冲中性产品（取消注释可恢复 /products/market-neutral/）
    # path(
    #     "products/market-neutral/",
    #     placeholder_views.placeholder_market_neutral,
    #     name="market_neutral_product",
    # ),
    path(
        "products/option-metrics/",
        option_metrics_views.option_metrics,
        name="option_metrics",
    ),
    path(
        "products/account-brief/",
        account_brief_views.account_brief,
        name="account_brief",
    ),
    path(
        "products/daily-report/",
        position_daily_views.index_view,
        name="position_daily",
    ),
    path(
        "products/daily-report/report/latest/",
        position_daily_views.report_view,
        {"trade_date": "latest"},
        name="position_daily_report_latest",
    ),
    path(
        "products/daily-report/report/<str:trade_date>/",
        position_daily_views.report_view,
        name="position_daily_report_date",
    ),
    path("nav/raw/", nav_curve_views.raw_nav, name="raw_nav"),
    path("logs/rerun/", mail_logs_views.mail_log_rerun, name="mail_log_rerun"),
    path("logs/", mail_logs_views.mail_scheduler_logs, name="mail_logs"),
    path("logout/", auth_views.logout_view, name="logout"),
]

