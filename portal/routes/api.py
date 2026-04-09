from django.urls import path

from portal.views import alpha_daily_views, nav_curve_views

api_urlpatterns = [
    path(
        "api/alpha/daily/",
        alpha_daily_views.api_alpha_daily,
        name="api_alpha_daily",
    ),
    path(
        "api/alpha/import/",
        alpha_daily_views.api_alpha_import,
        name="api_alpha_import",
    ),
    path("api/nav/curve/", nav_curve_views.api_nav_curve, name="api_nav_curve"),
]

