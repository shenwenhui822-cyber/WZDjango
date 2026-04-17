from django.urls import path

from portal.views import (
    alpha_daily_views,
    auth_views,
    nav_curve_views,
    placeholder_views,
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
    path("alpha/t0/", placeholder_views.placeholder_t0, name="alpha_t0"),
    path(
        "products/market-neutral/",
        placeholder_views.placeholder_market_neutral,
        name="market_neutral_product",
    ),
    path("nav/raw/", nav_curve_views.raw_nav, name="raw_nav"),
    path("logout/", auth_views.logout_view, name="logout"),
]

