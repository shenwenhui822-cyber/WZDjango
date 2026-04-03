from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.index, name="index"),
    path("home/", views.home, name="home"),
    path("alpha/daily/", views.alpha_daily, name="alpha_daily"),
    path("logout/", views.logout_view, name="logout"),
]
