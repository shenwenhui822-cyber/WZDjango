"""认证相关视图（Auth Views）。"""

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from portal.auth_access import login_landing_url_name, next_url_allowed


def _safe_next_url(request, user) -> str:
    next_url = (request.GET.get("next") or "").strip()
    if next_url.startswith("/") and next_url_allowed(user, next_url):
        return next_url
    return reverse(login_landing_url_name(user))


def index(request):
    """登录入口视图。"""
    if request.user.is_authenticated:
        landing = login_landing_url_name(request.user)
        if landing == "portal:index":
            return render(
                request,
                "portal/login.html",
                {"error": "暂无可用页面权限，请联系管理员在 Admin 中分配。"},
                status=403,
            )
        return redirect(landing)

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect(_safe_next_url(request, user))
        error = "用户名或密码错误，请重试。"
        if username:
            inactive = (
                get_user_model()
                .objects.filter(username=username, is_active=False)
                .first()
            )
            if inactive is not None and inactive.check_password(password):
                error = "账号未激活，请在 Admin 中勾选「有效」后再登录。"
        return render(
            request,
            "portal/login.html",
            {"error": error},
            status=401,
        )

    return render(request, "portal/login.html")


@login_required(login_url="/")
def home(request):
    """首页跳转视图。"""
    landing = login_landing_url_name(request.user)
    if landing == "portal:index":
        return render(
            request,
            "portal/login.html",
            {"error": "暂无可用页面权限，请联系管理员在 Admin 中分配。"},
            status=403,
        )
    return redirect(landing)


def logout_view(request):
    """登出视图。"""
    logout(request)
    return redirect("portal:index")

