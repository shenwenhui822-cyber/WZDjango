"""门户页面访问权限（与 Django User/Group 权限配合）。"""

from __future__ import annotations

from functools import wraps
from typing import Callable

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.shortcuts import redirect, render
from django.urls import reverse

# 权限 codename（完整名 portal.<codename>）
PERM_VIEW_FUND_NAV = "portal.view_fund_nav"
PERM_VIEW_NAV_BENCH_COMPARE = "portal.view_nav_bench_compare"
PERM_VIEW_ALPHA_T0 = "portal.view_alpha_t0"
PERM_VIEW_OPTION_METRICS = "portal.view_option_metrics"

# 登录后落地页顺序；next= 路径前缀校验
PORTAL_PAGE_ACCESS: tuple[tuple[str, str, str], ...] = (
    (PERM_VIEW_FUND_NAV, "portal:raw_nav", "/nav/raw/"),
    (PERM_VIEW_NAV_BENCH_COMPARE, "portal:nav_bench_compare", "/nav/bench-compare/"),
    (PERM_VIEW_ALPHA_T0, "portal:alpha_t0", "/alpha/t0/"),
    (PERM_VIEW_OPTION_METRICS, "portal:option_metrics", "/products/option-metrics/"),
)


def user_has_portal_perm(user: AbstractBaseUser | AnonymousUser, perm: str) -> bool:
    if not getattr(user, "is_active", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user.has_perm(perm)


def user_can_view_fund_nav(user: AbstractBaseUser | AnonymousUser) -> bool:
    return user_has_portal_perm(user, PERM_VIEW_FUND_NAV)


def user_can_view_nav_bench_compare(user: AbstractBaseUser | AnonymousUser) -> bool:
    return user_has_portal_perm(user, PERM_VIEW_NAV_BENCH_COMPARE)


def user_can_view_alpha_t0(user: AbstractBaseUser | AnonymousUser) -> bool:
    return user_has_portal_perm(user, PERM_VIEW_ALPHA_T0)


def user_can_view_option_metrics(user: AbstractBaseUser | AnonymousUser) -> bool:
    return user_has_portal_perm(user, PERM_VIEW_OPTION_METRICS)


def login_landing_url_name(user: AbstractBaseUser | AnonymousUser) -> str:
    """登录后进入第一个有权限的页面；均无权限则 portal:index。"""
    for perm, url_name, _path in PORTAL_PAGE_ACCESS:
        if user_has_portal_perm(user, perm):
            return url_name
    return "portal:index"


def _redirect_for_denied(request):
    landing = login_landing_url_name(request.user)
    if landing != "portal:index":
        return redirect(reverse(landing))
    return render(
        request,
        "portal/login.html",
        {"error": "暂无可用页面权限，请联系管理员在 Admin 中分配。"},
        status=403,
    )


def portal_page_required(perm: str) -> Callable:
    """须登录且具备指定门户页权限，否则跳转至其它有权限页面。"""

    def decorator(view_func: Callable) -> Callable:
        @login_required(login_url="/")
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not user_has_portal_perm(request.user, perm):
                return _redirect_for_denied(request)
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def fund_nav_page_required(view_func: Callable) -> Callable:
    return portal_page_required(PERM_VIEW_FUND_NAV)(view_func)


def next_url_allowed(user: AbstractBaseUser | AnonymousUser, next_url: str) -> bool:
    path = (next_url or "").split("?", 1)[0]
    if not path.startswith("/"):
        return False
    for perm, _url_name, prefix in PORTAL_PAGE_ACCESS:
        if path.startswith(prefix):
            return user_has_portal_perm(user, perm)
    return True
