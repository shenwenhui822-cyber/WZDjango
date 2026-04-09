from django.urls import path

from portal.views import (
    alpha_daily_views,
    auth_views,
    nav_curve_views,
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
    path("nav/raw/", nav_curve_views.raw_nav, name="raw_nav"),
    path("nav/t0/", nav_curve_views.t0_nav, name="t0_nav"),
    path("logout/", auth_views.logout_view, name="logout"),
]

