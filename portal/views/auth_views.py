"""认证相关视图（Auth Views）。"""

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render


def index(request):
    """登录入口视图。"""
    if request.user.is_authenticated:
        return redirect("portal:nav_curve")

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.GET.get("next") or "/nav/curve/"
            if not next_url.startswith("/"):
                next_url = "/nav/curve/"
            return redirect(next_url)
        return render(
            request,
            "portal/login.html",
            {"error": "用户名或密码错误，请重试。"},
            status=401,
        )

    return render(request, "portal/login.html")


@login_required(login_url="/")
def home(request):
    """首页跳转视图。"""
    return redirect("portal:nav_curve")


def logout_view(request):
    """登出视图。"""
    logout(request)
    return redirect("portal:index")

