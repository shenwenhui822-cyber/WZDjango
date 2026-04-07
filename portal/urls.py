from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.index, name="index"),
    path("home/", views.home, name="home"),
    path("alpha/daily/", views.alpha_daily, name="alpha_daily"),
    path("nav/raw/", views.raw_nav, name="raw_nav"),
    path("nav/t0/", views.t0_nav, name="t0_nav"),
    path("alpha/daily/import/", views.alpha_daily_import, name="alpha_daily_import"),
    path("api/alpha/daily/", views.api_alpha_daily, name="api_alpha_daily"),
    path("api/alpha/import/", views.api_alpha_import, name="api_alpha_import"),
    path("nav/curve/", views.nav_curve, name="nav_curve"),
    path("api/nav/curve/", views.api_nav_curve, name="api_nav_curve"),
    path("logout/", views.logout_view, name="logout"),
]
